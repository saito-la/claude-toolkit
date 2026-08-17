/**
 * deck-shots.cjs — スライドHTMLの各スライドをPNGに焼く（deck2pdf.py --rasterize の下請け）
 *
 * ベクター印刷（Chrome の --print-to-pdf）では `background-clip: text` のような
 * 装飾が PDF 内で画像化される過程で崩れることがある（実測: グラデーション見出しの
 * 後半が矩形の塗りになった）。画面描画のスクリーンショットは崩れないため、
 * 忠実さを優先する場合はこちらを使う。
 *
 * Usage:
 *   NODE_PATH=$(npm root -g) node deck-shots.cjs <html> <outdir> <waitMs> <selector> <scale>
 * 出力: <outdir>/slide-01.png ... と、1行のJSON（{count, width, height}）を stdout へ
 *
 * 依存: playwright（グローバル導入でよい。NODE_PATH で解決する）
 */

const path = require('path');
const { pathToFileURL } = require('url');

function loadPlaywright() {
  try {
    return require('playwright');
  } catch (e) {
    console.error('ERROR: playwright を解決できません。`npm i -g playwright` の上、'
      + 'NODE_PATH=$(npm root -g) を付けて実行してください。');
    process.exit(2);
  }
}

(async () => {
  const [html, outdir, waitMs, selector, scale] = process.argv.slice(2);
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: Number(scale) || 2,
  });
  await page.goto(pathToFileURL(path.resolve(html)).href, { waitUntil: 'load' });
  await page.waitForTimeout(Number(waitMs) || 8000);
  // フォント適用待ち。待たずに撮るとフォールバックフォントで焼き付く。
  await page.evaluate(() => document.fonts && document.fonts.ready);
  // 画面表示のままだと、デッキは「現在のスライド」以外を非表示にしているため
  // 要素スクリーンショットが not visible で失敗する。印刷メディアでは全スライドが
  // 展開されるので、そちらに切り替えてから撮る。
  await page.emulateMedia({ media: 'print' });
  await page.waitForTimeout(1500);

  const els = await page.$$(selector || 'section');
  if (els.length === 0) {
    console.error(`ERROR: セレクタ '${selector}' に一致する要素がありません`);
    await browser.close();
    process.exit(3);
  }
  // 印刷メディアに切り替えると、デッキが内部で使う不可視の同名要素（印刷用ステージや
  // スピーカーノートの器）が混ざることがある。実寸を持つものだけをスライドとして扱う。
  const visible = [];
  for (const el of els) {
    const b = await el.boundingBox();
    if (b && b.width >= 100 && b.height >= 100) visible.push({ el, box: b });
  }
  if (visible.length === 0) {
    console.error(`ERROR: セレクタ '${selector}' に実寸を持つ要素がありません`);
    await browser.close();
    process.exit(3);
  }
  const box = visible[0].box;
  for (let i = 0; i < visible.length; i++) {
    const file = path.join(outdir, `slide-${String(i + 1).padStart(2, '0')}.png`);
    try {
      await visible[i].el.screenshot({ path: file, timeout: 20000 });
    } catch (e) {
      console.error(`ERROR: スライド ${i + 1} を撮影できませんでした（${String(e).split('\n')[0]}）。`
        + '--slide-selector の指定を見直してください。');
      await browser.close();
      process.exit(4);
    }
  }
  await browser.close();
  console.log(JSON.stringify({
    count: visible.length,
    width: Math.round(box ? box.width : 1600),
    height: Math.round(box ? box.height : 900),
  }));
})();
