#!/usr/bin/env python3
"""md 内の相対リンク `[ラベル](パス)` のリンク先が存在するかを検査する。

使い方: check-md-links.py <md ファイルまたはディレクトリ>...
リンク切れがあれば一覧を出して終了コード 1、無ければ何も出さず 0。
対象外: http(s)・mailto・#アンカーのみ・コードブロック内。
規約の正本: instructions/principles.md「md 内のリポジトリ内ファイルへの参照」
"""
import re
import sys
import urllib.parse
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\((<[^>]+>|[^)\s]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def md_files(args):
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(p.rglob("*.md"))
        elif p.is_file():
            yield p


def check(path):
    broken, in_code, seen = [], False, 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in LINK.finditer(line):
            t = m.group(1).strip("<>")
            if re.match(r"^(https?:|mailto:|#)", t):
                continue
            seen += 1
            rel = urllib.parse.unquote(t.split("#", 1)[0])
            if rel and not (path.parent / rel).exists():
                broken.append((n, t))
    return broken, seen


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    files, total, bad = 0, 0, 0
    for f in md_files(sys.argv[1:]):
        files += 1
        broken, seen = check(f)
        total += seen
        for n, t in broken:
            bad += 1
            print(f"{f}:{n}: リンク切れ {t}")
    print(f"検査 {files} ファイル・相対リンク {total} 件・切れ {bad} 件", file=sys.stderr)
    sys.exit(1 if bad else 0)


main()
