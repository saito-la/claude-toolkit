#!/usr/bin/env python3
"""
deck2pdf.py — スライドHTML → PDF（Chrome印刷、1スライド=1ページ）

Claude Design 等が書き出す standalone HTML のスライドデッキを、レイアウトを崩さず
PDF にする。md2pdf.py（文書PDF）と対になるスライド版。

なぜ「同じブラウザで印刷する」のか:
  デッキの見た目は HTML/CSS のレイアウトエンジンが作っている。別経路の変換
  （生成サービス側のPDF書き出し、pptx へのエクスポート）はレイアウトを別の
  エンジンで組み直すため、グラデーション文字が落ちる・テキスト幅が変わって
  不要な改行が入る、といった崩れが出る。表示に使ったのと同じ Chromium に
  そのまま印刷させるのが最も忠実。

パイプラインと、過去に踏んだ落とし穴への対処を内蔵する：
  1. 入力HTMLの末尾に印刷用CSSを追記した一時HTMLを作る。
     - `@page { size: <幅>px <高さ>px; margin: 0 }` でページをスライドの CSS ピクセル
       寸法に合わせる。A4等に載せるとスケーリングでリフローが起き、テキストの
       折り返し位置が変わる。
     - **重要**: `print-color-adjust: exact` を入れる。Chrome の --print-to-pdf は
       背景色・背景画像を既定で落とすため、これが無いと帯や塗りが消える。
     - CSS はドキュメント順で後勝ちなので </body> 直前に置く。ただしデッキ本体を
       JS で描画するエクスポート（Claude Design 等）では、JS が後から挿入する
       スタイルの方がさらに後になる。**その場合 --size は効かず、デッキ自身の
       @page 指定が採用される**（実測: 1920x1080 相当で出力された）。出力後に
       表示される実寸で確認すること。仕上がり自体はどちらでも崩れない。
  2. Chrome ヘッドレスで HTML → PDF（`--no-pdf-header-footer`）。
     `--virtual-time-budget` でフォント・画像・起動アニメーションの適用を待つ。
  3. 検証。`--dump-dom` でJS実行後のDOMを取り、スライド要素の数を数えて
     PDFのページ数と突き合わせる（不一致なら警告し --force-breaks を案内）。

Usage:
    python3 deck2pdf.py deck.html [-o out.pdf] [--size 1600x900] [--wait 8000]
                                  [--slide-selector section] [--force-breaks]
                                  [--no-verify]

依存: Google Chrome(等のChromium系) / PyMuPDF(fitz, ページ数検証用・任意)

確認（送付前に必ず）:
    qlmanage -t -s 1600 -o <出力先> out.pdf   # 1ページ目をPNG化して目視
"""

from __future__ import annotations  # 3.9 系でも int|None 等の注釈を評価させない

import argparse
import base64
import json
import re
import shutil
import subprocess
import os
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

_WIN_ROOTS = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
_WIN_RELPATHS = [
    r"Google\Chrome\Application\chrome.exe",
    r"Google\Chrome SxS\Application\chrome.exe",
    r"Chromium\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
]
if sys.platform == "win32":
    CHROME_CANDIDATES = [
        str(Path(root) / rel) for root in _WIN_ROOTS if root for rel in _WIN_RELPATHS
    ]

CHROME_COMMANDS = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "chrome", "msedge", "brave",
]

PRINT_CSS = """
<style id="deck2pdf-print">
@page { size: __W__px __H__px; margin: 0; }
@media print {
  html, body { margin: 0 !important; padding: 0 !important; }
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
}
</style>
"""

FORCE_BREAK_CSS = """
<style id="deck2pdf-breaks">
@media print {
  __SEL__ { break-after: page; break-inside: avoid; }
  __SEL__:last-of-type { break-after: auto; }
}
</style>
"""


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for cmd in CHROME_COMMANDS:
        found = shutil.which(cmd)
        if found:
            return found
    print("ERROR: Google Chrome 等の Chromium 系ブラウザが見つかりません。", file=sys.stderr)
    sys.exit(1)


def parse_size(spec: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", spec)
    if not m:
        print(f"ERROR: --size は 1600x900 の形式で指定してください（受領: {spec}）", file=sys.stderr)
        sys.exit(1)
    return int(m.group(1)), int(m.group(2))


def inject_css(html_text: str, css: str) -> str:
    """CSS を </body> 直前へ差し込む。ドキュメント順で後勝ちにするため末尾寄りに置く。"""
    idx = html_text.lower().rfind("</body>")
    if idx == -1:
        return html_text + css
    return html_text[:idx] + css + html_text[idx:]


def count_slides(chrome: str, html_uri: str, selector: str, wait_ms: int) -> int | None:
    """JS実行後のDOMからスライド要素数を数える。数えられなければ None。

    生HTMLの正規表現では、スクリプト内の文字列に同じタグが現れる形式（バンドル済み
    エクスポート等）で誤カウントする。レンダリング後のDOMを見る必要がある。
    """
    r = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--dump-dom",
         f"--virtual-time-budget={wait_ms}", html_uri],
        capture_output=True, text=True)
    dom = r.stdout
    if not dom:
        return None
    tag, _, cls = selector.partition(".")
    tag = tag.strip() or "section"
    if cls:
        pat = re.compile(rf"<{re.escape(tag)}\b[^>]*\bclass=\"[^\"]*\b{re.escape(cls)}\b", re.I)
    else:
        pat = re.compile(rf"<{re.escape(tag)}\b", re.I)
    return len(pat.findall(dom))


def count_pdf_pages(pdf_path: Path) -> int | None:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        data = pdf_path.read_bytes()
        hits = re.findall(rb"/Type\s*/Page[^s]", data)
        return len(hits) or None
    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    doc.close()
    return n


RASTER_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
@page { size: __W__px __H__px; margin: 0; }
html, body { margin: 0; padding: 0; background: #fff; }
img { display: block; width: __W__px; height: __H__px; break-after: page; }
img:last-of-type { break-after: auto; }
</style></head><body>
__IMGS__
</body></html>
"""


def rasterize(src: Path, workdir: Path, wait_ms: int, selector: str, scale: float) -> tuple[list, dict]:
    """各スライドをPNGに焼き、ファイル一覧とメタ情報を返す（Playwright 必須）。"""
    shots = workdir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parent / "deck-shots.cjs"
    if not script.exists():
        print(f"ERROR: {script} が見つかりません", file=sys.stderr)
        sys.exit(1)
    env = os.environ.copy()
    if not env.get("NODE_PATH"):
        r = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            env["NODE_PATH"] = r.stdout.strip()
    r = subprocess.run(
        ["node", str(script), str(src), str(shots), str(wait_ms), selector, str(scale)],
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"スライドの描画に失敗しました:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    meta = json.loads(r.stdout.strip().splitlines()[-1])
    return sorted(shots.glob("slide-*.png")), meta


def build_raster_html(pngs: list, w: int, h: int, title: str) -> str:
    imgs = []
    for p in pngs:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        imgs.append(f'<img src="data:image/png;base64,{b64}" alt="">')
    return (RASTER_HTML.replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__TITLE__", title).replace("__IMGS__", "\n".join(imgs)))


def read_page_size(pdf_path: Path) -> str | None:
    """出力PDFの実ページ寸法を "1440x810pt (1920x1080px)" の形で返す。

    --size がデッキ側の @page に負けることがあるため、意図した寸法で出たかを
    出力時に示す。
    """
    m = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
                  pdf_path.read_bytes())
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    wpt, hpt = x1 - x0, y1 - y0
    return f"{wpt:.0f}x{hpt:.0f}pt（{wpt * 96 / 72:.0f}x{hpt * 96 / 72:.0f}px 相当）"


def main() -> None:
    ap = argparse.ArgumentParser(description="スライドHTML → PDF（1スライド=1ページ・背景保持）")
    ap.add_argument("input", help="入力 HTML（standalone を推奨。外部依存があると欠ける）")
    ap.add_argument("-o", "--output", help="出力 PDF パス（省略時は入力と同名 .pdf）")
    ap.add_argument("--size", default="1600x900",
                    help="スライドのCSSピクセル寸法（既定 1600x900）。デッキ側に @page 指定が"
                         "あるとそちらが優先される（出力時に実寸を表示する）")
    ap.add_argument("--wait", type=int, default=8000,
                    help="レンダリング待ちの仮想時間ms（既定 8000）。重いデッキは増やす")
    ap.add_argument("--slide-selector", default="section",
                    help="スライド1枚に対応する要素（既定 section。div.slide のようにclass指定も可）")
    ap.add_argument("--force-breaks", action="store_true",
                    help="スライド要素ごとに強制改ページするCSSを注入する（ページ数が合わないとき）")
    ap.add_argument("--rasterize", action="store_true",
                    help="各スライドを画面描画のPNGに焼いてからPDFにする（要 playwright）。"
                         "グラデーション文字など、ベクター印刷で崩れる装飾がある場合に使う")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="--rasterize 時の解像度倍率（既定 2.0）。上げるほど鮮明・大容量")
    ap.add_argument("--no-verify", action="store_true", help="ページ数の検証をしない")
    args = ap.parse_args()

    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: {src} が見つかりません", file=sys.stderr)
        sys.exit(1)
    out = (Path(args.output).expanduser().resolve() if args.output else src.with_suffix(".pdf"))
    w, h = parse_size(args.size)

    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        if args.rasterize:
            pngs, meta = rasterize(src, td_path, args.wait, args.slide_selector, args.scale)
            w = int(meta.get("width") or w)
            h = int(meta.get("height") or h)
            patched = build_raster_html(pngs, w, h, src.stem)
            slides = len(pngs)
        else:
            html_text = src.read_text(encoding="utf-8", errors="surrogateescape")
            css = PRINT_CSS.replace("__W__", str(w)).replace("__H__", str(h))
            if args.force_breaks:
                css += FORCE_BREAK_CSS.replace("__SEL__", args.slide_selector)
            patched = inject_css(html_text, css)
            slides = None
        # 一時HTMLは入力と同じディレクトリ名を持たないため、外部参照（相対パスの画像・
        # フォント）があると解決できない。standalone HTML を前提にする。
        tmp_html = td_path / "deck.html"
        tmp_html.write_text(patched, encoding="utf-8", errors="surrogateescape")
        uri = tmp_html.as_uri()
        r = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--virtual-time-budget={args.wait}", f"--print-to-pdf={out}", uri],
            capture_output=True, text=True)
        if not out.exists():
            print(f"Chrome 印刷に失敗しました:\n{r.stderr}", file=sys.stderr)
            sys.exit(1)

        pages = count_pdf_pages(out)
        if slides is None and not args.no_verify:
            slides = count_slides(chrome, uri, args.slide_selector, args.wait)

    if pages is None:
        print("注意: PDFのページ数を数えられませんでした（PyMuPDF未導入かつ解析不能）", file=sys.stderr)
    elif slides is not None:
        if slides == 0:
            print(f"注意: セレクタ '{args.slide_selector}' に一致する要素がDOMにありません。"
                  f"--slide-selector を指定してください（PDFは {pages} ページ）", file=sys.stderr)
        elif pages != slides:
            print(f"注意: スライド {slides} 枚に対し PDF は {pages} ページです。"
                  f"--force-breaks を付けて再実行すると1枚=1ページに揃うことがあります", file=sys.stderr)

    size = read_page_size(out)
    detail = "、".join(x for x in [f"{pages} ページ" if pages else "", size or ""] if x)
    print(f"✓ {out}" + (f"（{detail}）" if detail else ""))
    print(f"  確認: qlmanage -t -s 1600 -o {out.parent} {out}")


if __name__ == "__main__":
    main()
