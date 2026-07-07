#!/usr/bin/env python3
"""
combine-pdfs.py — 複数の markdown ファイルを、表紙・目次・PDFしおり（ブックマーク）・
通しページ番号付きの1冊のPDFにまとめる。

md2pdf.py で個別に PDF 化した「調査ダイジェスト」「添付資料集」等を束ねる用途を想定。
~/Projects/law の「添付資料集」分冊（表紙＋目次＋しおり＋通しページ番号）で確立した
手順をスクリプト化したもの。

パイプライン:
  1. 各入力mdを md2pdf.py 経由でPDF化（ページ番号なし）。見出し（H1）はタイトルとして
     そのまま流用し、書き換えない（しおり側にのみ「資料N　タイトル」を付与）。
  2. 各PDFの頁数から開始頁を計算し、表紙件（タイトル・目次）を生成・PDF化。
     目次の行数次第で表紙が複数頁になる場合があるため、実際の頁数で最大3回まで再計算する。
  3. PyMuPDF (fitz) で 表紙+各PDF を結合し、PDFしおり（アウトライン）を設定。
  4. 結合後のPDF全体に通しページ番号「n / N」をスタンプする（md2pdf.py と同じ書式）。

Usage:
    python3 combine-pdfs.py -o out.pdf --title "海外制度調査 参考資料集" \\
        [--subtitle "米国編"] [--author "東京大学医学部附属病院 臨床研究推進センター"] \\
        [--style style.html] [--bookmark-prefix "資料"] \\
        file1.md file2.md file3.md ...

各mdファイルのタイトルは、そのファイル1行目の "# " 見出しから自動取得する。
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MD2PDF = SCRIPT_DIR / "md2pdf.py"


def get_h1(md_path: Path) -> str:
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return md_path.stem


def render_md(md_path: Path, out_pdf: Path, style: Path | None) -> None:
    cmd = [sys.executable, str(MD2PDF), str(md_path), "-o", str(out_pdf), "--no-page-numbers"]
    if style:
        cmd += ["--style", str(style)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_pdf.exists():
        print(f"md2pdf 失敗: {md_path}\n{r.stderr}", file=sys.stderr)
        sys.exit(1)


def rewrite_named_links(pdf_path) -> None:
    """PDF内の名前付き内部リンク（kind=4、id属性によるアンカー）を、
    ページ番号ベースのGOTOリンク（kind=1）に変換して上書き保存する。
    fitz.insert_pdf() は名前付きリンクの参照先（/Dests名前ツリー）を結合先に
    引き継がないため、結合前に変換しておかないとPDF結合時にリンクが失われる。
    参照: 本ファイル冒頭のコメント通り md2pdf.py の出力（Chrome生成PDF）は
    HTMLの <a id> アンカーを名前付き宛先として書き出す。"""
    import fitz
    doc = fitz.open(str(pdf_path))
    names = doc.resolve_names()
    if not names:
        doc.close()
        return
    changed = False
    for page in doc:
        to_add = []
        for link in list(page.get_links()):
            if link.get("kind") == 4 and link.get("nameddest") in names:
                dest = names[link["nameddest"]]
                to_add.append({
                    "kind": fitz.LINK_GOTO, "from": link["from"],
                    "page": dest["page"], "to": fitz.Point(*dest["to"]),
                    "zoom": dest.get("zoom", 0.0),
                })
        if not to_add:
            continue
        changed = True
        while True:
            cur = [l for l in page.get_links() if l.get("kind") == 4]
            if not cur:
                break
            page.delete_link(cur[0])
        for nl in to_add:
            page.insert_link(nl)
    if changed:
        tmp = Path(str(pdf_path) + ".tmp")
        doc.save(str(tmp), deflate=True)
        doc.close()
        tmp.replace(pdf_path)
    else:
        doc.close()


def build_cover_md(title: str, subtitle: str | None, author: str | None, date: str | None,
                    entries: list[tuple[str, int]]) -> str:
    lines = [f"# {title}"]
    meta = [m for m in (subtitle, date, author) if m]
    for i, m in enumerate(meta):
        lines.append(m + ("\\" if i < len(meta) - 1 else ""))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 目次")
    lines.append("")
    for label, start_page in entries:
        lines.append(f"- {label} ……… p.{start_page}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="複数mdをPDF化し、表紙・目次・しおり・通しページ番号付きで結合する")
    ap.add_argument("inputs", nargs="+", help="結合するmdファイル（この順で結合される）")
    ap.add_argument("-o", "--output", required=True, help="出力PDFパス")
    ap.add_argument("--title", required=True, help="表紙の主題名")
    ap.add_argument("--subtitle", default=None, help="表紙の副題（分冊名等）")
    ap.add_argument("--author", default=None, help="表紙に記載する著者・組織名")
    ap.add_argument("--date", default=None, help="表紙に記載する日付（呼び出し側で指定。省略時は表示しない）")
    ap.add_argument("--style", default=None, help="全パート・表紙に適用するCSSファイル（省略時は各mdの兄弟style.html自動検出）")
    ap.add_argument("--bookmark-prefix", default="資料", help="PDFしおりの接頭辞（既定: 資料 → 資料1, 資料2, ...）")
    ap.add_argument("--no-cover", action="store_true", help="表紙・目次を生成せず、指定順にPDFを結合するだけにする")
    args = ap.parse_args()

    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("ERROR: PyMuPDF が必要です（pip install pymupdf）", file=sys.stderr)
        sys.exit(1)

    md_paths = [Path(p).expanduser().resolve() for p in args.inputs]
    for p in md_paths:
        if not p.exists():
            print(f"ERROR: {p} が見つかりません", file=sys.stderr)
            sys.exit(1)
    style = Path(args.style).expanduser().resolve() if args.style else None
    out = Path(args.output).expanduser().resolve()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        titles = [get_h1(p) for p in md_paths]

        # 1. 各パートをページ番号なしでPDF化
        part_pdfs = []
        for i, p in enumerate(md_paths, start=1):
            out_pdf = td / f"part-{i}.pdf"
            render_md(p, out_pdf, style)
            rewrite_named_links(out_pdf)  # 結合前に内部アンカーリンクを変換（失われるのを防ぐ）
            part_pdfs.append(out_pdf)
        part_counts = [fitz.open(str(pp)).page_count for pp in part_pdfs]

        cover_pdf = None
        cover_count = 0
        if not args.no_cover:
            # 2. 開始頁を仮決定 → 表紙を生成 → 実際の頁数で再計算（最大3回収束）
            for _ in range(3):
                start_pages, cur = [], cover_count + 1
                for c in part_counts:
                    start_pages.append(cur)
                    cur += c
                entries = [(f"{args.bookmark_prefix}{i}　{t}", sp)
                           for i, (t, sp) in enumerate(zip(titles, start_pages), start=1)]
                cover_md_text = build_cover_md(args.title, args.subtitle, args.author, args.date, entries)
                cover_md_path = td / "cover.md"
                cover_md_path.write_text(cover_md_text, encoding="utf-8")
                cover_pdf = td / "cover.pdf"
                render_md(cover_md_path, cover_pdf, style)
                new_count = fitz.open(str(cover_pdf)).page_count
                if new_count == cover_count:
                    break
                cover_count = new_count
        else:
            start_pages, cur = [], 1
            for c in part_counts:
                start_pages.append(cur)
                cur += c

        # 3. 結合 + しおり
        combined = fitz.open()
        toc = []
        if cover_pdf:
            combined.insert_pdf(fitz.open(str(cover_pdf)))
            toc.append([1, args.title, 1])
        for i, (pp, t, sp) in enumerate(zip(part_pdfs, titles, start_pages), start=1):
            combined.insert_pdf(fitz.open(str(pp)))
            toc.append([1, f"{args.bookmark_prefix}{i}　{t}", sp])
        combined.set_toc(toc)
        combined.save(str(out), deflate=True)
        combined.close()

    # 4. 通しページ番号
    doc = fitz.open(str(out))
    n = doc.page_count
    for i, page in enumerate(doc):
        r = page.rect
        txt = f"{i + 1} / {n}"
        tw = fitz.get_text_length(txt, fontsize=9)
        page.insert_text(((r.width - tw) / 2.0, r.height - 28.0), txt,
                          fontsize=9, fontname="helv", color=(0.4, 0.4, 0.4))
    tmp = out.with_suffix(".numbered.tmp.pdf")
    doc.save(str(tmp), deflate=True)
    doc.close()
    tmp.replace(out)

    print(f"✓ {out}（全{n}頁、{len(md_paths)}パート）")


if __name__ == "__main__":
    main()
