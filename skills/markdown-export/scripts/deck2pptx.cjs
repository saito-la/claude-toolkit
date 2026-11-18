#!/usr/bin/env node
/* deck2pptx.cjs — スライドHTML → PowerPoint（文字を編集できる図形とテキストボックス、発表者ノート付き）
 *
 * ブラウザ（Chromium）がデッキを描画した結果を読み取り、各要素の位置・大きさ・色・文字を
 * そのまま PowerPoint の図形とテキストボックスに写す。レイアウトエンジンは HTML 側のものだけを
 * 使い、PowerPoint 側で組み直さないので、HTML と同じ配置になる。文字は編集できる。
 *
 * 前提とするデッキの構造（Saito Deck System 等の 1920×1080 固定キャンバス）:
 *   - <deck-stage width="1920" height="1080"> の直下に 1スライド = 1 <section>
 *   - location.hash = '#n' で n 枚目だけが表示される（表示中の section の矩形を読む）
 *   - 発表者ノートは各 section の <aside class="notes">
 *
 * 写し方:
 *   - 背景色・枠線を持つ要素 → 図形（角丸は rectRadius、正円は ellipse、辺ごとの枠線は細い矩形）
 *   - 背景画像（data URI）→ 画像、linear-gradient の硬い段差 → 色の帯
 *   - <svg>・<img>・<canvas> → その要素だけを透過 PNG に撮って画像として置く（図は編集できないが形は崩れない）
 *   - 破線・点線の枠線 → PowerPoint の破線・点線
 *   - ブロック要素の中のインライン文字列 → 1つのテキストボックス（太字・色・サイズ・等幅をランで保持）
 *   - 文字の折り返し幅は要素の内容幅に合わせる（書体が違うと折り返し位置はわずかに変わる）
 *
 * 固定キャンバスのページを縦に並べただけの HTML（組織図など。<div class="slide"> が 1000×750px で並ぶ）は
 * --slides でページの要素を指定する。1ページずつほかのページを隠して読み、用紙の幅は --width-in（既定は
 * deck-stage なら 13.333in、--slides なら 10in）、高さはページの縦横比から決める。
 *
 * --native-lines を付けると、直線だけの SVG（<path> の M/L/H/V と <line>・<polyline>、塗り無し・矢印無し）を
 * 画像に撮らず、線分ごとの PowerPoint の直線にする。組織図の接続線のように、受け取った人が線を動かして
 * 直す図に使う。曲線・塗り・矢印を含む SVG は従来どおり画像にする。
 *
 * Usage:
 *   NODE_PATH="$(npm root -g):<pptxgenjs を持つ node_modules>" \
 *     node deck2pptx.cjs deck.html [-o deck.pptx] [--font "BIZ UDPGothic"] [--mono Consolas] [--author "..."]
 *     node deck2pptx.cjs chart.html --slides '.slide' --native-lines [--width-in 10] [--title "..."]
 *       [--latin Calibri] [--lang ja-JP]
 *
 * --latin は欧文の書体を和文と別にする（既定は --font と同じ）。BIZ UDPGothic の欧文は幅が広く、英語の
 * ページは HTML より大きく折り返すので、英語を含む図では Calibri 等の幅の狭い書体を指定する。
 * --lang は文字の言語（既定 en-US）。ja-JP にすると PowerPoint が日本語の禁則（行頭の「、」を避ける）を効かせる。
 *
 * 依存: playwright（グローバル導入でよい）、pptxgenjs（NODE_PATH で解決する）
 * 確認: 出力した pptx は office2pdf.py で PDF にして全ページを目視する
 */
const path = require('path');
const { pathToFileURL } = require('url');

function loadDep(name) {
  try { return require(name); } catch (e) {
    console.error(`ERROR: ${name} を解決できません。NODE_PATH に ${name} を持つ node_modules を含めてください。`);
    process.exit(2);
  }
}
const { chromium } = loadDep('playwright');
const PptxGenJS = loadDep('pptxgenjs');

const args = process.argv.slice(2);
if (!args.length || args.includes('-h') || args.includes('--help')) {
  console.log('usage: deck2pptx.cjs deck.html [-o out.pptx] [--font FONT] [--mono FONT] [--author TEXT]\n'
    + '                     [--slides SELECTOR] [--width-in INCH] [--native-lines] [--title TEXT] [--latin FONT] [--lang LANG]');
  process.exit(0);
}
const opt = { font: 'BIZ UDPGothic', mono: 'Consolas', author: '' };
let html = null;
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '-o') opt.out = args[++i];
  else if (a === '--font') opt.font = args[++i];
  else if (a === '--mono') opt.mono = args[++i];
  else if (a === '--author') opt.author = args[++i];
  else if (a === '--slides') opt.slides = args[++i];
  else if (a === '--width-in') opt.widthIn = parseFloat(args[++i]);
  else if (a === '--native-lines') opt.nativeLines = true;
  else if (a === '--title') opt.title = args[++i];
  else if (a === '--latin') opt.latin = args[++i];
  else if (a === '--lang') opt.lang = args[++i];
  else html = a;
}
const HTML = path.resolve(html);
const OUT = path.resolve(opt.out || HTML.replace(/\.html?$/i, '') + '.pptx');
let PX_PER_IN = 144; // 1920px = 13.333in（LAYOUT_WIDE）。--slides のときはページの幅と --width-in から決め直す
const px2in = (v) => v / PX_PER_IN;
const px2pt = (v) => v * 72 / PX_PER_IN;
const SEL = opt.slides || 'deck-stage > section';

// ブラウザ側: 表示中の section を走査して図形・文字の一覧を返す
function extract(section, nativeLines) {
  const SR = section.getBoundingClientRect();
  const items = [];
  const parseColor = (s) => {
    const m = /rgba?\(([^)]+)\)/.exec(s || '');
    if (!m) return { hex: null, a: 0 };
    const p = m[1].split(',').map((x) => parseFloat(x));
    const a = p.length > 3 ? p[3] : 1;
    const hex = p.slice(0, 3).map((n) => Math.round(n).toString(16).padStart(2, '0')).join('').toUpperCase();
    return { hex, a };
  };
  const isBlock = (el) => {
    const d = getComputedStyle(el).display;
    return !(d.startsWith('inline') || d === 'contents');
  };
  const styleOf = (el) => {
    const s = getComputedStyle(el);
    return {
      size: parseFloat(s.fontSize),
      bold: parseInt(s.fontWeight, 10) >= 600,
      italic: s.fontStyle === 'italic',
      color: parseColor(s.color).hex,
      mono: /Mono|Menlo|Consolas|monospace/i.test(s.fontFamily),
    };
  };
  const rel = (r) => ({ x: r.left - SR.left, y: r.top - SR.top, w: r.width, h: r.height });

  // ブロック要素 el の直下にあるインラインの文字列を集める（ブロックの子孫で止まる）。
  // 文字列の間にブロックの子があると、CSS では前後の文字列が別の行（匿名ブロック）になるので、
  // そこで区切って別のまとまり（segs の要素）にする
  function collect(el, segs) {
    const cur = () => segs[segs.length - 1];
    const pre = getComputedStyle(el).whiteSpace.startsWith('pre');
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        let t = n.textContent;
        if (!pre) {
          t = t.replace(/[ \t\r\n]+/g, ' ');
          if (!t.trim()) continue;
        } else if (t === '') continue; // pre の中の空白だけの文字（コードの字下げ）も残す
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const rr of range.getClientRects()) if (rr.width > 0 && rr.height > 0) cur().rects.push(rr);
        cur().runs.push({ text: t, style: styleOf(el), pre });
      } else if (n.nodeType === 1) {
        if (n.tagName === 'BR') { cur().runs.push({ br: true }); continue; }
        const d = getComputedStyle(n).display;
        if (d === 'none') continue;
        if (isBlock(n)) {
          if (cur().runs.some((x) => x.text && x.text.trim())) segs.push({ runs: [], rects: [] });
          else cur().runs.length = 0; // ブロックの前の <br> だけの行は捨てる
          continue;
        }
        collect(n, segs);
      }
    }
  }

  function shapesOf(el, cs, r) {
    const bg = parseColor(cs.backgroundColor);
    const bgi = cs.backgroundImage || 'none';
    const radius = parseFloat(cs.borderTopLeftRadius) || 0;
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => ({
      w: parseFloat(cs['border' + s + 'Width']) || 0,
      c: parseColor(cs['border' + s + 'Color']),
      st: cs['border' + s + 'Style'],
    }));
    const vis = sides.map((b) => b.w > 0 && b.st !== 'none' && b.c.a > 0);
    const uniform = vis.every(Boolean) && sides.every((b) => b.w === sides[0].w && b.c.hex === sides[0].c.hex);
    const m = /url\("?(data:image\/[a-z]+;base64,[^")]+)"?\)/.exec(bgi);
    if (m) {
      items.push({ type: 'image', rect: r, data: m[1] });
    } else if (bgi.includes('linear-gradient')) {
      const stops = [];
      const re = /rgba?\([^)]+\)\s*([\d.]+)%/g;
      let mm;
      while ((mm = re.exec(bgi))) stops.push({ color: parseColor(mm[0]).hex, pct: parseFloat(mm[1]) });
      items.push({ type: 'gradient', rect: r, stops, radius });
    }
    if (bg.a > 0 || uniform) {
      items.push({
        type: 'shape', rect: r, radius,
        fill: bg.a > 0 ? bg.hex : null,
        line: uniform ? { color: sides[0].c.hex, w: sides[0].w, dash: sides[0].st } : null,
        ellipse: radius > 0 && radius >= Math.min(r.w, r.h) / 2 - 0.5,
        topRoundOnly: radius === 0 && el.parentElement && el === el.parentElement.firstElementChild
          && getComputedStyle(el.parentElement).overflow === 'hidden' && (parseFloat(getComputedStyle(el.parentElement).borderTopLeftRadius) || 0) > 0,
        parentRadius: el.parentElement ? (parseFloat(getComputedStyle(el.parentElement).borderTopLeftRadius) || 0) : 0,
      });
    }
    if (!uniform) sides.forEach((b, i) => { if (vis[i]) items.push({ type: 'edge', side: i, rect: r, w: b.w, color: b.c.hex }); });
  }

  // SVG が直線だけで描かれていれば、線分（ページ座標）の一覧を返す。直線以外を含めば null
  function svgLines(svg) {
    const shapes = [...svg.querySelectorAll('*')].filter((n) => !['g', 'title', 'desc', 'defs'].includes(n.tagName.toLowerCase()));
    if (!shapes.length) return []; // 中身の無い SVG は何も置かない（ページ全体の透明な画像を作らない）
    const out = [];
    for (const n of shapes) {
      const tag = n.tagName.toLowerCase();
      const cs = getComputedStyle(n);
      if (cs.display === 'none') continue;
      const fill = parseColor(cs.fill);
      if (fill.a > 0 && cs.fill !== 'none') return null;
      if ((cs.markerStart && cs.markerStart !== 'none') || (cs.markerEnd && cs.markerEnd !== 'none') || (cs.markerMid && cs.markerMid !== 'none')) return null;
      let pts = [];
      if (tag === 'line') {
        pts = [[[+n.getAttribute('x1'), +n.getAttribute('y1')], [+n.getAttribute('x2'), +n.getAttribute('y2')]]];
      } else if (tag === 'polyline') {
        const v = (n.getAttribute('points') || '').trim().split(/[\s,]+/).map(Number);
        const run = [];
        for (let k = 0; k + 1 < v.length; k += 2) run.push([v[k], v[k + 1]]);
        pts = [run];
      } else if (tag === 'path') {
        const tok = (n.getAttribute('d') || '').match(/[MLHVmlhvZz]|-?[\d.]+(?:e-?\d+)?/g) || [];
        if ((n.getAttribute('d') || '').replace(/[MLHVmlhvZz\d.,\s\-e]/g, '')) return null; // 曲線などの命令
        let cmd = null, cur = [0, 0], start = [0, 0], run = null;
        for (let k = 0; k < tok.length;) {
          if (/[A-Za-z]/.test(tok[k])) { cmd = tok[k++]; if (cmd === 'Z' || cmd === 'z') { if (run) run.push([...start]); continue; } }
          const num = () => parseFloat(tok[k++]);
          if (cmd === 'M' || cmd === 'm') {
            const x = num(), y = num();
            cur = cmd === 'm' ? [cur[0] + x, cur[1] + y] : [x, y];
            start = [...cur]; run = [[...cur]]; pts.push(run);
            cmd = cmd === 'm' ? 'l' : 'L';
          } else if (cmd === 'L' || cmd === 'l') {
            const x = num(), y = num();
            cur = cmd === 'l' ? [cur[0] + x, cur[1] + y] : [x, y]; run.push([...cur]);
          } else if (cmd === 'H' || cmd === 'h') {
            const x = num(); cur = [cmd === 'h' ? cur[0] + x : x, cur[1]]; run.push([...cur]);
          } else if (cmd === 'V' || cmd === 'v') {
            const y = num(); cur = [cur[0], cmd === 'v' ? cur[1] + y : y]; run.push([...cur]);
          } else return null;
        }
      } else return null;
      const stroke = parseColor(cs.stroke);
      if (!stroke.hex || stroke.a === 0 || cs.stroke === 'none') continue;
      const m = n.getScreenCTM();
      const scale = Math.hypot(m.a, m.b);
      const w = (parseFloat(cs.strokeWidth) || 1) * scale;
      const P = (q) => { const r = new DOMPoint(q[0], q[1]).matrixTransform(m); return [r.x - SR.left, r.y - SR.top]; };
      for (const run of pts) {
        for (let k = 0; k + 1 < run.length; k++) {
          const a = P(run[k]), b = P(run[k + 1]);
          if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 0.01) continue;
          out.push({ type: 'line', a, b, w, color: stroke.hex });
        }
      }
    }
    return out;
  }

  function visit(el) {
    if (['ASIDE', 'SCRIPT', 'STYLE', 'COL', 'COLGROUP'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = rel(el.getBoundingClientRect());
    // 直線だけの SVG は線分にする（--native-lines）
    if (nativeLines && el.tagName.toLowerCase() === 'svg') {
      const segs = svgLines(el);
      if (segs) { items.push(...segs); return; }
    }
    // 図（SVG・画像・canvas）は要素だけを撮って画像にする。中は辿らない
    if (['svg', 'img', 'canvas'].includes(el.tagName.toLowerCase())) {
      if (r.w > 0 && r.h > 0) {
        window.__d2pN = (window.__d2pN || 0) + 1;
        const id = 'r' + window.__d2pN;
        el.setAttribute('data-d2p', id);
        items.push({ type: 'raster', rect: r, id, abs: { x: r.x + SR.left, y: r.y + SR.top } });
      }
      return;
    }
    const inline = !isBlock(el);
    if (r.w > 0 && r.h > 0 && el !== section) {
      shapesOf(el, cs, r);
      // インライン要素の文字は、最も近いブロックの祖先が既に集めている
      if (!inline) {
        const segs = [{ runs: [], rects: [] }];
        collect(el, segs);
        for (const { runs, rects } of segs) {
        // 先頭・末尾の <br> は、ブロックの子の前後の改行なので落とす
        while (runs.length && runs[0].br) runs.shift();
        while (runs.length && runs[runs.length - 1].br) runs.pop();
        if (runs.some((x) => x.text && x.text.trim()) && rects.length) {
          const top = Math.min(...rects.map((q) => q.top)), bottom = Math.max(...rects.map((q) => q.bottom));
          const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
          const bL = parseFloat(cs.borderLeftWidth) || 0, bR = parseFloat(cs.borderRightWidth) || 0;
          let align = cs.textAlign;
          if (align === 'start' || align === 'left' || align === '') align = 'left';
          else if (align === 'end') align = 'right';
          let box = { x: r.x + bL + padL, y: top - SR.top, w: r.w - padL - padR - bL - bR, h: bottom - top };
          // flex コンテナ直下の文字列は flex item として配置されている（兄弟の要素と並ぶ）ので、
          // 描画された矩形そのものを箱にする
          if (cs.display.includes('flex')) {
            const left = Math.min(...rects.map((q) => q.left)), right = Math.max(...rects.map((q) => q.right));
            box = { x: left - SR.left, y: top - SR.top, w: right - left, h: bottom - top };
            align = 'left';
          }
          // HTML で何行に折り返されているか（同じ高さにある矩形は1行と数える）
          const tops = [];
          for (const q of rects) if (!tops.some((t) => Math.abs(t - q.top) < 4)) tops.push(q.top);
          items.push({
            type: 'text', align, runs, box,
            lineHeight: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.3,
            listItem: cs.display === 'list-item',
            // 番号付きの箇条（ol）は番号と開始番号を、字下げは親の左の余白（マーカーの置き場）を写す
            listNum: cs.display === 'list-item' && /decimal/.test(cs.listStyleType)
              ? [...el.parentElement.children].filter((c) => c.tagName === 'LI').indexOf(el) + 1 : 0,
            listIndent: cs.display === 'list-item' && el.parentElement ? (parseFloat(getComputedStyle(el.parentElement).paddingLeft) || 0) : 0,
            lines: tops.length,
          });
        }
        }
      }
    }
    if (inline) return;
    for (const c of el.children) visit(c);
  }
  visit(section);
  const notes = section.querySelector('aside.notes');
  const title = section.querySelector('.slide-title, .s1-h1, h1, h2');
  return {
    items,
    notes: notes ? notes.textContent.trim().replace(/\n\s+/g, '\n') : '',
    title: title ? title.textContent.trim() : section.dataset.screenLabel || '',
  };
}

(async () => {
  const browser = await chromium.launch();
  // 図を撮るときの解像度のために 2 倍で描く（配置の座標は CSS の px のまま）
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  const stage = !opt.slides;
  await page.goto(pathToFileURL(HTML).href, { waitUntil: 'load' });
  await page.waitForTimeout(3000);
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.evaluate((src) => { window.__deck2pptxExtract = src; }, extract.toString());
  // 図を撮るとき、対象の要素以外を見えなくし、背景を透過にする
  await page.addStyleTag({ content: `
    html.d2p-raster, html.d2p-raster body, html.d2p-raster deck-stage, html.d2p-raster deck-stage > section { background: transparent !important; }
    html.d2p-raster deck-stage > section * { visibility: hidden !important; }
    html.d2p-raster [data-d2p-on], html.d2p-raster [data-d2p-on] * { visibility: visible !important; }` });
  // --slides: 1ページずつ、ほかのページを隠して先頭に置く（座標と画像の撮影位置を deck-stage の場合と揃える）
  await page.addStyleTag({ content: `.d2p-off { display: none !important; } html.d2p-one, html.d2p-one body { margin: 0 !important; padding: 0 !important; }
    html.d2p-raster ${SEL} { background: transparent !important; }
    html.d2p-raster ${SEL} * { visibility: hidden !important; }` });
  const n = await page.evaluate((sel) => document.querySelectorAll(sel).length, SEL);
  if (!n) { console.error(`ERROR: ${SEL} が見つかりません`); process.exit(1); }

  const pptx = new PptxGenJS();
  if (stage) {
    pptx.layout = 'LAYOUT_WIDE';
  } else {
    const size = await page.evaluate((sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return { w: r.width, h: r.height }; }, SEL);
    const wIn = opt.widthIn || 10;
    PX_PER_IN = size.w / wIn;
    pptx.defineLayout({ name: 'D2P', width: wIn, height: size.h / PX_PER_IN });
    pptx.layout = 'D2P';
  }
  if (opt.author) pptx.author = opt.author;
  const t = opt.title || await page.title();
  if (t) pptx.title = t;

  for (let i = 1; i <= n; i++) {
    if (stage) {
      await page.evaluate((k) => { location.hash = '#' + k; }, i);
    } else {
      await page.evaluate(({ k, sel }) => {
        document.documentElement.classList.add('d2p-one');
        document.querySelectorAll(sel).forEach((s, j) => s.classList.toggle('d2p-off', j !== k - 1));
        window.scrollTo(0, 0);
      }, { k: i, sel: SEL });
    }
    await page.waitForTimeout(150);
    const d = await page.evaluate(({ k, sel, nl }) => {
      const sec = document.querySelectorAll(sel)[k - 1];
      return (0, eval)('(' + window.__deck2pptxExtract + ')')(sec, nl);
    }, { k: i, sel: SEL, nl: !!opt.nativeLines });
    for (const it of d.items) {
      if (it.type !== 'raster') continue;
      await page.evaluate((id) => {
        document.documentElement.classList.add('d2p-raster');
        document.querySelector(`[data-d2p="${id}"]`).setAttribute('data-d2p-on', '');
      }, it.id);
      const buf = await page.screenshot({ clip: { x: it.abs.x, y: it.abs.y, width: it.rect.w, height: it.rect.h }, omitBackground: true });
      await page.evaluate((id) => {
        document.documentElement.classList.remove('d2p-raster');
        document.querySelector(`[data-d2p="${id}"]`).removeAttribute('data-d2p-on');
      }, it.id);
      it.data = 'data:image/png;base64,' + buf.toString('base64');
    }
    const slide = pptx.addSlide();
    slide.background = { color: 'FFFFFF' };
    for (const it of d.items) {
      const R = it.rect || it.box;
      if (it.type === 'raster') {
        slide.addImage({ data: it.data, x: px2in(R.x), y: px2in(R.y), w: px2in(R.w), h: px2in(R.h) });
      } else if (it.type === 'line') {
        // 角が欠けないよう、線幅の半分だけ両端を延ばす（PowerPoint の既定の端は平ら）
        let [ax, ay] = it.a, [bx, by] = it.b;
        const L = Math.hypot(bx - ax, by - ay), ex = (bx - ax) / L * it.w / 2, ey = (by - ay) / L * it.w / 2;
        ax -= ex; ay -= ey; bx += ex; by += ey;
        slide.addShape(pptx.ShapeType.line, {
          x: px2in(Math.min(ax, bx)), y: px2in(Math.min(ay, by)), w: px2in(Math.abs(bx - ax)), h: px2in(Math.abs(by - ay)),
          flipH: bx < ax, flipV: by < ay, line: { color: it.color, width: px2pt(it.w) },
        });
      } else if (it.type === 'image') {
        slide.addImage({ data: it.data, x: px2in(R.x), y: px2in(R.y), w: px2in(R.w), h: px2in(R.h), sizing: { type: 'contain', w: px2in(R.w), h: px2in(R.h) } });
      } else if (it.type === 'gradient') {
        for (let s = 0; s + 1 < it.stops.length; s += 2) {
          const a = it.stops[s], b = it.stops[s + 1];
          slide.addShape(pptx.ShapeType.rect, {
            x: px2in(R.x + R.w * a.pct / 100), y: px2in(R.y), w: px2in(R.w * (b.pct - a.pct) / 100), h: px2in(R.h),
            fill: { color: a.color }, line: { color: a.color, width: 0 },
          });
        }
      } else if (it.type === 'shape') {
        const base = { x: px2in(R.x), y: px2in(R.y), w: px2in(R.w), h: px2in(R.h) };
        const style = {
          fill: it.fill ? { color: it.fill } : { color: 'FFFFFF', transparency: 100 },
          line: it.line ? { color: it.line.color, width: px2pt(it.line.w),
            ...(it.line.dash === 'dashed' ? { dashType: 'dash' } : it.line.dash === 'dotted' ? { dashType: 'sysDot' } : {}) }
            : { color: 'FFFFFF', width: 0, transparency: 100 },
        };
        if (it.ellipse) {
          slide.addShape(pptx.ShapeType.ellipse, { ...base, ...style });
        } else if (it.topRoundOnly) {
          slide.addShape(pptx.ShapeType.roundRect, { ...base, ...style, rectRadius: px2in(it.parentRadius) });
          slide.addShape(pptx.ShapeType.rect, { ...base, y: px2in(R.y + R.h / 2), h: px2in(R.h / 2), ...style });
        } else if (it.radius > 0) {
          slide.addShape(pptx.ShapeType.roundRect, { ...base, ...style, rectRadius: px2in(it.radius) });
        } else {
          slide.addShape(pptx.ShapeType.rect, { ...base, ...style });
        }
      } else if (it.type === 'edge') {
        const e = [
          { x: R.x, y: R.y, w: R.w, h: it.w },
          { x: R.x + R.w - it.w, y: R.y, w: it.w, h: R.h },
          { x: R.x, y: R.y + R.h - it.w, w: R.w, h: it.w },
          { x: R.x, y: R.y, w: it.w, h: R.h },
        ][it.side];
        slide.addShape(pptx.ShapeType.rect, { x: px2in(e.x), y: px2in(e.y), w: px2in(e.w), h: px2in(e.h), fill: { color: it.color }, line: { color: it.color, width: 0 } });
      } else if (it.type === 'text') {
        const runs = [];
        for (const rn of it.runs) {
          if (rn.br) { if (runs.length) runs[runs.length - 1].options.breakLine = true; else runs.push({ text: '', options: { breakLine: true } }); continue; }
          const parts = rn.pre ? rn.text.split('\n') : [rn.text];
          parts.forEach((p, idx) => {
            if (idx > 0 && runs.length) runs[runs.length - 1].options.breakLine = true;
            if (p === '' && idx > 0) return;
            runs.push({ text: p, options: {
              fontSize: px2pt(rn.style.size), bold: rn.style.bold, italic: rn.style.italic,
              color: rn.style.color || '212121', fontFace: rn.style.mono ? opt.mono : opt.font,
              ...(opt.lang ? { lang: opt.lang } : {}),
            } });
          });
        }
        if (!runs.length) continue;
        // white-space: pre の文字（コードの字下げ）は行頭の空白を残す
        if (!it.runs.some((x) => x.pre)) runs[0].text = runs[0].text.replace(/^\s+/, '');
        runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/\s+$/, '');
        const B = { ...it.box };
        const shift = it.listItem ? (it.listIndent || 34) : 0;
        // HTML で1行に収まっていた文字と、<br> の改行だけで行を分けていた文字は折り返させない
        // （書体の違いで幅が少し変わるため、箱も広げる）
        const hard = runs.filter((x) => x.options.breakLine).length;
        const single = it.lines === hard + 1;
        const oneLine = it.lines === 1 && hard === 0;
        if (single) {
          const extra = B.w * 0.25 + 20;
          if (it.align === 'center') B.x -= extra / 2;
          else if (it.align === 'right') B.x -= extra;
          B.w += extra;
        }
        // 行送りが字の大きさの 1.3 倍に満たない複数行の文字は、PowerPoint では上へずれて描かれる
        // （行送り 1.2 倍の小さな注記で約 3px）。上下中央に置いて HTML の字面の位置に合わせる
        const maxSize = Math.max(...it.runs.filter((x) => x.style).map((x) => x.style.size));
        const tight = !oneLine && it.lineHeight < maxSize * 1.3;
        // 1行の文字は行送りを指定せず、描かれた字面の矩形の上下中央に置く
        // （行送りを指定すると、行の高さの余白の分だけ字が下へずれる。丸の中の数字などで目立つ）
        slide.addText(runs, {
          x: px2in(B.x - shift), y: px2in(B.y - 2), w: px2in(B.w + shift), h: px2in(B.h + 6),
          align: it.align, valign: oneLine || tight ? 'middle' : 'top', margin: 0, wrap: !single, fit: single ? 'none' : 'shrink',
          ...(oneLine ? {} : { lineSpacing: px2pt(it.lineHeight) }), paraSpaceAfter: 0, paraSpaceBefore: 0,
          bullet: it.listItem ? { indent: px2pt(shift), ...(it.listNum ? { type: 'number', numberStartAt: it.listNum } : {}) } : false,
        });
      }
    }
    if (d.notes) slide.addNotes(d.notes);
    console.error(`slide ${i}: ${d.items.length} items  ${d.title}`);
  }
  await browser.close();
  await pptx.writeFile({ fileName: OUT });
  // 欧文の書体を和文と分ける（pptxgenjs は latin と ea に同じ書体を書く）
  if (opt.latin && opt.latin !== opt.font) {
    const fs = require('fs');
    const JSZip = loadDep('jszip');
    const zip = await JSZip.loadAsync(fs.readFileSync(OUT));
    const esc = opt.font.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`<a:latin typeface="${esc}"`, 'g');
    for (const name of Object.keys(zip.files).filter((f) => /^ppt\/slides\/slide\d+\.xml$/.test(f))) {
      const xml = await zip.file(name).async('string');
      zip.file(name, xml.replace(re, `<a:latin typeface="${opt.latin}"`));
    }
    fs.writeFileSync(OUT, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
  }
  console.log(OUT);
})();
