// ブラウザ（Chrome・Edge）のアイコンに「AI 用」の印とプロファイル名の帯を重ねた PNG を作る。
// 使い方: osascript -l JavaScript make-icon.js <アプリ> <出力PNG> <帯の文字> <帯の色 #RRGGBB>
//   <アプリ> は /Applications/Google Chrome.app のようなアプリのパス。アイコンは Finder と同じものを OS から取る
// macOS に最初から入っている JXA で描くので、Xcode の Command Line Tools が無い端末でも動く。
// 呼び出し元は claude-chrome.sh（README.md「アイコン」）。
ObjC.import('AppKit');

function run(argv) {
  if (argv.length !== 4) throw new Error('usage: make-icon.js <app> <out.png> <label> <#RRGGBB>');
  const [app, outPath, label, hex] = argv;
  if (!$.NSFileManager.defaultManager.fileExistsAtPath(app)) throw new Error(`アプリがありません: ${app}`);
  const base = $.NSWorkspace.sharedWorkspace.iconForFile(app);

  const S = 1024;
  const rep = $.NSBitmapImageRep.alloc
    .initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null, S, S, 8, 4, true, false, 'NSDeviceRGBColorSpace', 0, 0);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));

  base.drawInRect($.NSMakeRect(0, 0, S, S));

  const color = (h) => {
    const v = parseInt(h.replace('#', ''), 16) || 0;
    return $.NSColor.colorWithSRGBRedGreenBlueAlpha(((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255, 1);
  };
  const attrs = (size) => {
    const d = $.NSMutableDictionary.alloc.init;
    d.setObjectForKey($.NSFont.systemFontOfSizeWeight(size, 0.56 /* NSFontWeightHeavy */), $('NSFont')); // NSFontAttributeName
    d.setObjectForKey($.NSColor.whiteColor, $('NSColor')); // NSForegroundColorAttributeName
    return d;
  };
  const textSize = (s, size) => $(s).sizeWithAttributes(attrs(size));
  const drawText = (s, x, y, w, h, size) => {
    const t = textSize(s, size);
    $(s).drawAtPointWithAttributes($.NSMakePoint(x + (w - t.width) / 2, y + (h - t.height) / 2), attrs(size));
  };
  const fillAndEdge = (path, fill) => {
    color(fill).setFill;
    path.fill;
    $.NSColor.whiteColor.setStroke;
    path.setLineWidth(18);
    path.stroke;
  };

  // 下の帯＝プロファイル（色と文字で区別する）
  const band = { x: 60, y: 40, w: S - 120, h: 300 };
  fillAndEdge($.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(band.x, band.y, band.w, band.h), 90, 90), hex);
  let size = 220;
  while (size > 80 && textSize(label, size).width > band.w - 80) size -= 10;
  drawText(label, band.x, band.y, band.w, band.h, size);

  // 左上の丸＝AI 用の印（全プロファイル共通。人が使う Chrome には付かない）
  const dot = { x: 30, y: S - 330, w: 300, h: 300 };
  fillAndEdge($.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(dot.x, dot.y, dot.w, dot.h)), '#D97757');
  drawText('AI', dot.x, dot.y, dot.w, dot.h, 150);

  $.NSGraphicsContext.restoreGraphicsState;
  const png = rep.representationUsingTypeProperties(4 /* NSBitmapImageFileTypePNG */, $());
  if (!png.writeToFileAtomically(outPath, true)) throw new Error(`書き出せません: ${outPath}`);
}
