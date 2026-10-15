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
 *   - ブロック要素の中のインライン文字列 → 1つのテキストボックス（太字・色・サイズ・等幅をランで保持）
 *   - 文字の折り返し幅は要素の内容幅に合わせる（書体が違うと折り返し位置はわずかに変わる）
 *
 * Usage:
 *   NODE_PATH="$(npm root -g):<pptxgenjs を持つ node_modules>" \
 *     node deck2pptx.cjs deck.html [-o deck.pptx] [--font "BIZ UDPGothic"] [--mono Consolas] [--author "..."]
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
  console.log('usage: deck2pptx.cjs deck.html [-o out.pptx] [--font FONT] [--mono FONT] [--author TEXT]');
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
  else html = a;
}
const HTML = path.resolve(html);
const OUT = path.resolve(opt.out || HTML.replace(/\.html?$/i, '') + '.pptx');
const PX_PER_IN = 144; // 1920px = 13.333in（LAYOUT_WIDE）
const px2in = (v) => v / PX_PER_IN;
const px2pt = (v) => v * 0.5;

// ブラウザ側: 表示中の section を走査して図形・文字の一覧を返す
function extract(section) {
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

  // ブロック要素 el の直下にあるインラインの文字列を集める（ブロックの子孫で止まる）
  function collect(el, runs, rects) {
    const pre = getComputedStyle(el).whiteSpace.startsWith('pre');
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        let t = n.textContent;
        if (!pre) {
          t = t.replace(/[ \t\r\n]+/g, ' ');
          if (!t.trim()) continue;
        } else if (!t.replace(/\n/g, '').trim() && !t.includes('\n')) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const rr of range.getClientRects()) if (rr.width > 0 && rr.height > 0) rects.push(rr);
        runs.push({ text: t, style: styleOf(el), pre });
      } else if (n.nodeType === 1) {
        if (n.tagName === 'BR') { runs.push({ br: true }); continue; }
        const d = getComputedStyle(n).display;
        if (d === 'none') continue;
        if (isBlock(n)) continue;
        collect(n, runs, rects);
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
        line: uniform ? { color: sides[0].c.hex, w: sides[0].w } : null,
        ellipse: radius > 0 && radius >= Math.min(r.w, r.h) / 2 - 0.5,
        topRoundOnly: radius === 0 && el.parentElement && el === el.parentElement.firstElementChild
          && getComputedStyle(el.parentElement).overflow === 'hidden' && (parseFloat(getComputedStyle(el.parentElement).borderTopLeftRadius) || 0) > 0,
        parentRadius: el.parentElement ? (parseFloat(getComputedStyle(el.parentElement).borderTopLeftRadius) || 0) : 0,
      });
    }
    if (!uniform) sides.forEach((b, i) => { if (vis[i]) items.push({ type: 'edge', side: i, rect: r, w: b.w, color: b.c.hex }); });
  }

  function visit(el) {
    if (['ASIDE', 'SCRIPT', 'STYLE', 'COL', 'COLGROUP'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = rel(el.getBoundingClientRect());
    const inline = !isBlock(el);
    if (r.w > 0 && r.h > 0 && el !== section) {
      shapesOf(el, cs, r);
      // インライン要素の文字は、最も近いブロックの祖先が既に集めている
      if (!inline) {
        const runs = [], rects = [];
        collect(el, runs, rects);
        if (runs.some((x) => x.text && x.text.trim())) {
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
            lines: tops.length,
          });
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
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(HTML).href, { waitUntil: 'load' });
  await page.waitForTimeout(3000);
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.evaluate((src) => { window.__deck2pptxExtract = src; }, extract.toString());
  const n = await page.evaluate(() => document.querySelectorAll('deck-stage > section').length);
  if (!n) { console.error('ERROR: deck-stage > section が見つかりません'); process.exit(1); }

  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  if (opt.author) pptx.author = opt.author;
  const t = await page.title();
  if (t) pptx.title = t;

  for (let i = 1; i <= n; i++) {
    await page.evaluate((k) => { location.hash = '#' + k; }, i);
    await page.waitForTimeout(150);
    const d = await page.evaluate((k) => {
      const sec = document.querySelectorAll('deck-stage > section')[k - 1];
      return (0, eval)('(' + window.__deck2pptxExtract + ')')(sec);
    }, i);
    const slide = pptx.addSlide();
    slide.background = { color: 'FFFFFF' };
    for (const it of d.items) {
      const R = it.rect || it.box;
      if (it.type === 'image') {
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
          line: it.line ? { color: it.line.color, width: px2pt(it.line.w) } : { color: 'FFFFFF', width: 0, transparency: 100 },
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
            } });
          });
        }
        if (!runs.length) continue;
        runs[0].text = runs[0].text.replace(/^\s+/, '');
        runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/\s+$/, '');
        const B = { ...it.box };
        const shift = it.listItem ? 34 : 0;
        // HTML で1行に収まっていた文字は折り返させない（書体の違いで幅が少し変わるため、箱も広げる）
        const single = it.lines === 1 && !runs.some((x) => x.options.breakLine);
        if (single) {
          const extra = B.w * 0.25 + 20;
          if (it.align === 'center') B.x -= extra / 2;
          else if (it.align === 'right') B.x -= extra;
          B.w += extra;
        }
        slide.addText(runs, {
          x: px2in(B.x - shift), y: px2in(B.y - 2), w: px2in(B.w + shift), h: px2in(B.h + 6),
          align: it.align, valign: 'top', margin: 0, wrap: !single, fit: single ? 'none' : 'shrink',
          lineSpacing: px2pt(it.lineHeight), paraSpaceAfter: 0, paraSpaceBefore: 0,
          bullet: it.listItem ? { indent: px2pt(34) } : false,
        });
      }
    }
    if (d.notes) slide.addNotes(d.notes);
    console.error(`slide ${i}: ${d.items.length} items  ${d.title}`);
  }
  await browser.close();
  await pptx.writeFile({ fileName: OUT });
  console.log(OUT);
})();
