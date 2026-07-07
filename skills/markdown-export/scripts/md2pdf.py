#!/usr/bin/env python3
"""
md2pdf.py — markdown → 整形PDF（pandoc → 整形HTML → Chrome印刷 → ページ番号）

全プロジェクト共通の「読みやすい日本語PDF」生成スクリプト。
md2docx.py（docx）と対になる PDF 版。

パイプラインと、過去に踏んだ落とし穴への対処を内蔵する：
  1. pandoc で md → standalone HTML（CSS を <head> に埋め込み）。
     - **重要**: pandoc 標準テンプレCSSの `body{max-width:36em;padding:50px}` を
       上書きして本文を全幅化する。これを忘れると右余白が極端に広くなる。
     - 可視タイトルの二重表示を避けるため `title` でなく `pagetitle` を使う。
     - URL は `-f markdown+autolink_bare_uris` により、`<url>` だけでなく単独行の
       素の URL も `<a href>` に自動リンク化され、Chrome印刷でクリック可能なリンク
       注釈として保持される（濃紺・下線で表示）。裸URLを書くだけでクリック可能。
  2. Google Chrome ヘッドレスで HTML → PDF（`--no-pdf-header-footer`）。
     A4・余白既定20mm（4辺均等）・内容依存の表列幅（pandocの等幅colgroupを無効化）。
  3. PyMuPDF(fitz) で各ページ下中央に「n / N」のページ番号をスタンプ
     （Chrome印刷はヘッダ/フッタの細かな制御ができないため後段で付与。
      fitz 挿入テキストは pdftotext で拾えないことがあるが実ビューアでは表示される）。

Usage:
    python3 md2pdf.py input.md [-o out.pdf] [--margin 20mm]
                               [--no-page-numbers] [--title "ブラウザタブ用タイトル"]

依存: pandoc / Google Chrome(等のChromium系) / PyMuPDF(fitz, ページ番号用・任意)

注意（文書側の作法）:
  - 見出しに括弧書きの副題を付けない（living-doc 規約）。
  - 金額は本文側で円換算を併記する（このスクリプトは換算しない）。
  - 参考文献を小さめ・コンパクトにしたい場合は md 側で `::: refs … :::`
    （pandoc fenced div）で囲む。CSS の `.refs` が適用される。
"""

import argparse
import shutil
import subprocess
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

CSS_TEMPLATE = """<style>
@page { size: A4; margin: __MARGIN__; }
* { box-sizing: border-box; }
html { margin: 0; padding: 0; }
body {
  font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  font-size: 10.5pt; line-height: 1.7; color: #1a1a1a;
  margin: 0 !important; padding: 0 !important; max-width: none !important;
}
h1 { font-size: 16pt; line-height: 1.4; margin: 0 0 .2em;
     border-bottom: 2.5px solid #20406a; padding-bottom: .25em; color: #16335a; }
h1 + p { color: #555; font-size: 11pt; margin: .1em 0; }
h2 { font-size: 13pt; margin: 1.4em 0 .45em; padding: .15em 0 .15em .5em;
     border-left: 5px solid #20406a; color: #16335a; }
h3 { font-size: 11.5pt; margin: 1em 0 .3em; color: #20406a; }
p { margin: .42em 0; overflow-wrap: anywhere; }
ul, ol { margin: .35em 0; padding-left: 1.5em; }
li { margin: .22em 0; }
strong { color: #11283f; }
blockquote { background: #f4f6f9; border-left: 4px solid #8aa0bd;
             margin: .7em 0; padding: .5em .9em; color: #333; font-size: 10pt; }
table { border-collapse: collapse; width: 100%; margin: .55em 0; font-size: 8.8pt; table-layout: auto; }
table col { width: auto !important; }
th, td { border: 1px solid #9aa6b3; padding: 3px 6px; vertical-align: top; text-align: left; line-height: 1.4; }
th { background: #e9eef4; color: #16335a; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.1em 0; }
code { background: #eef1f4; padding: 0 3px; border-radius: 3px; font-size: .9em; }
a { color: #0b3d91; text-decoration: underline; word-break: break-all; }
.refs { font-size: 9pt; line-height: 1.5; }
.refs p { margin: .1em 0 .32em; }
h2, h3 { break-after: avoid; }
table, blockquote { break-inside: avoid; }
</style>"""


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    print("ERROR: Google Chrome 等の Chromium 系ブラウザが見つかりません。", file=sys.stderr)
    sys.exit(1)


def stamp_page_numbers(pdf_path: Path) -> bool:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("注意: PyMuPDF(fitz) 未導入のためページ番号をスキップ（pip install pymupdf）", file=sys.stderr)
        return False
    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    for i, page in enumerate(doc):
        r = page.rect
        txt = f"{i + 1} / {n}"
        tw = fitz.get_text_length(txt, fontsize=9)
        page.insert_text(((r.width - tw) / 2.0, r.height - 28.0), txt,
                         fontsize=9, fontname="helv", color=(0.4, 0.4, 0.4))
    tmp = pdf_path.with_suffix(".numbered.tmp.pdf")
    doc.save(str(tmp), deflate=True)
    doc.close()
    shutil.move(str(tmp), str(pdf_path))
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="markdown → 整形PDF（A4・余白既定2cm・全幅・ページ番号）")
    ap.add_argument("input", help="入力 markdown ファイル")
    ap.add_argument("-o", "--output", help="出力 PDF パス（省略時は入力と同名 .pdf）")
    ap.add_argument("--margin", default="20mm", help="ページ余白（4辺均等、既定 20mm）")
    ap.add_argument("--title", default=None, help="ブラウザタブ/PDFメタ用タイトル（本文H1とは別。可視タイトルは二重表示しない）")
    ap.add_argument("--no-page-numbers", action="store_true", help="下中央のページ番号を付けない")
    ap.add_argument("--style", default=None, help="適用するCSSファイルを明示指定（兄弟<stem>.style.html自動検出より優先。combine-pdfs.py等の外部ツールから使用）")
    args = ap.parse_args()

    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: {src} が見つかりません", file=sys.stderr)
        sys.exit(1)
    out = (Path(args.output).expanduser().resolve() if args.output else src.with_suffix(".pdf"))
    pagetitle = args.title or src.stem

    with tempfile.TemporaryDirectory() as td:
        css = Path(td) / "style.html"
        css.write_text(CSS_TEMPLATE.replace("__MARGIN__", args.margin), encoding="utf-8")
        html = Path(td) / "doc.html"
        # 既定CSSに続けて、入力と同名の兄弟 <stem>.style.html があれば追加include。
        # 後勝ちで既定を上書きできる＝文書固有のスタイルをCSSファイルに集約できる。
        includes = [f"--include-in-header={css}"]
        if args.style:
            extra_css = Path(args.style).expanduser().resolve()
            if not extra_css.exists():
                print(f"ERROR: --style {extra_css} が見つかりません", file=sys.stderr)
                sys.exit(1)
            includes.append(f"--include-in-header={extra_css}")
        else:
            sibling_css = src.with_name(src.stem + ".style.html")
            if sibling_css.exists():
                includes.append(f"--include-in-header={sibling_css}")
        # pandoc: md → standalone HTML（CSSを<head>へ、pagetitleのみ設定＝可視タイトル二重化を回避）
        r = subprocess.run(
            ["pandoc", str(src), "-f", "markdown+autolink_bare_uris", "-s",
             *includes,
             "-M", f"pagetitle={pagetitle}", "-o", str(html)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"pandoc エラー:\n{r.stderr}", file=sys.stderr)
            sys.exit(r.returncode)
        # Chrome: HTML → PDF
        chrome = find_chrome()
        r = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={out}", html.as_uri()],
            capture_output=True, text=True)
        if not out.exists():
            print(f"Chrome 印刷に失敗しました:\n{r.stderr}", file=sys.stderr)
            sys.exit(1)

    if not args.no_page_numbers:
        stamp_page_numbers(out)
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
