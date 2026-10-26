#!/usr/bin/env python3
"""
office2pdf.py — PowerPoint（.pptx）・Excel（.xlsx）→ PDF（Microsoft Office 本体で書き出す。macOS）

pptx・xlsx を「相手が PowerPoint / Excel で開いたときの見た目」で確かめるために、
Office 本体に PDF を書き出させる。別のレイアウトエンジン（LibreOffice 等）で描くと
折り返しや図形の位置が変わるので、本物で描く。docx は `docx-word-check.sh`（Word に
ページ数も数えさせる）を使う。

落とし穴と対処（2026-10-15〜16 に踏んだ。Word 用の docx-word-check.sh は 2026-09-23 から同じ対処）:
  - Office の各アプリはサンドボックスの中で動くため、AppleScript で手元のパスを直接 open すると
    「Grant File Access」ダイアログが出て止まる。そのダイアログが残っている間は、あとから
    どんな方法で開いても失敗する（AppleEvent timed out / -9074 / 開いたはずの文書が missing value）。
  - 対処は、ファイルをアプリ自身のコンテナ
      ~/Library/Containers/com.microsoft.Powerpoint/Data/  （PowerPoint）
      ~/Library/Containers/com.microsoft.Excel/Data/       （Excel）
      ~/Library/Containers/com.microsoft.Word/Data/        （Word）
    の中へ写してから開き、同じ場所へ PDF を書かせること。コンテナの中はダイアログ無しで
    読み書きできる。書き出したら手元へ写して、コンテナの作業ファイルは消す。
  - 開いた文書は「1番目の文書」ではなく名前で指す。利用者が同じアプリで別の文書を開いていると、
    1番目がそちらになり、利用者の文書を PDF にしたうえ保存せずに閉じてしまう（2026-10-27、
    開いていた Budget Sheet が閉じられ、別の表が PDF になった）。
  - ダイアログで固まったアプリが残っていると進まないので、開いている文書が無ければ
    先にそのアプリを終了させる（--restart）。

Usage:
    python3 office2pdf.py deck.pptx [-o out.pdf] [--png DIR [--scale 1.6]] [--restart]
    python3 office2pdf.py book.xlsx [-o out.pdf] [--png DIR] [--restart]

  --png DIR   PDF の各ページを PNG にして DIR に置く（要 pymupdf）。目視確認用
  --restart   先にそのアプリを終了してから開く（ダイアログで固まっているとき）

確認（送付前に必ず）: --png で出した各ページを全部見る。1ページ目だけで判断しない。
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import time
import uuid

HOME = pathlib.Path.home()

APPS = {
    ".pptx": {
        "name": "Microsoft PowerPoint",
        "container": HOME / "Library/Containers/com.microsoft.Powerpoint/Data",
        "script": r'''
on run argv
  set inPath to item 1 of argv
  set outPath to item 2 of argv
  set docName to item 3 of argv
  set hfsOut to (POSIX file outPath) as string
  tell application "Microsoft PowerPoint"
    with timeout of 60 seconds
      open (POSIX file inPath)
    end timeout
    set t to 0
    repeat until (exists presentation docName) or t > 30
      delay 1
      set t to t + 1
    end repeat
    if not (exists presentation docName) then error "presentation did not open"
    set p to presentation docName
    with timeout of 180 seconds
      save p in hfsOut as save as PDF
    end timeout
    close p saving no
    return "ok"
  end tell
end run
''',
    },
    ".xlsx": {
        "name": "Microsoft Excel",
        "container": HOME / "Library/Containers/com.microsoft.Excel/Data",
        "script": r'''
on run argv
  set inPath to item 1 of argv
  set outPath to item 2 of argv
  set docName to item 3 of argv
  set hfsOut to (POSIX file outPath) as string
  tell application "Microsoft Excel"
    with timeout of 60 seconds
      open (POSIX file inPath)
    end timeout
    set t to 0
    repeat until (exists workbook docName) or t > 30
      delay 1
      set t to t + 1
    end repeat
    if not (exists workbook docName) then error "workbook did not open"
    set wb to workbook docName
    with timeout of 180 seconds
      save workbook as wb filename hfsOut file format PDF file format
    end timeout
    close wb saving no
    return "ok"
  end tell
end run
''',
    },
}


def main():
    ap = argparse.ArgumentParser(description="pptx / xlsx → PDF via Microsoft Office (macOS)")
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--png", metavar="DIR")
    ap.add_argument("--scale", type=float, default=1.6)
    ap.add_argument("--restart", action="store_true")
    a = ap.parse_args()

    src = pathlib.Path(a.src).resolve()
    if not src.exists():
        sys.exit(f"not found: {src}")
    ext = src.suffix.lower()
    if ext == ".docx":
        sys.exit("docx は docx-word-check.sh を使ってください（Word にページ数も数えさせます）")
    app = APPS.get(ext)
    if not app:
        sys.exit(f"対応していない拡張子です: {ext}（.pptx / .xlsx）")
    out = pathlib.Path(a.out).resolve() if a.out else src.with_suffix(".pdf")
    if not app["container"].exists():
        sys.exit(f"{app['name']} のコンテナが見つかりません: {app['container']}")
    if a.restart:
        subprocess.run(["pkill", "-x", app["name"]], check=False)
        time.sleep(3)

    work = app["container"] / "tmp" / f"office2pdf-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        wsrc = work / (work.name + ext)  # 利用者が開いている文書と名前で区別できるように一意の名前にする
        wout = work / "out.pdf"
        shutil.copy2(src, wsrc)
        script = work / "export.applescript"
        script.write_text(app["script"], encoding="utf-8")
        r = subprocess.run(["osascript", str(script), str(wsrc), str(wout), wsrc.name], capture_output=True, text=True, timeout=400)
        if r.returncode != 0 or not wout.exists():
            msg = (r.stderr or r.stdout).strip()
            sys.exit(
                f"{app['name']} の書き出しに失敗しました: {msg}\n"
                "  アプリに「Grant File Access」等のダイアログが残っているなら --restart を付けて再実行してください。"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wout, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    pages = None
    if a.png:
        try:
            import pymupdf  # type: ignore
        except ImportError:
            sys.exit("--png には pymupdf が要ります: pip install pymupdf")
        d = pymupdf.open(str(out))
        pages = d.page_count
        png_dir = pathlib.Path(a.png)
        png_dir.mkdir(parents=True, exist_ok=True)
        for i, pg in enumerate(d, 1):
            pg.get_pixmap(matrix=pymupdf.Matrix(a.scale, a.scale)).save(str(png_dir / f"page-{i:02d}.png"))
        print(f"png: {png_dir} ({pages} pages)")
    print(f"✓ {out}" + (f"（{pages} ページ）" if pages else ""))


if __name__ == "__main__":
    main()
