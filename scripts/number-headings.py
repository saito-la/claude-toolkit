#!/usr/bin/env python3
"""md の見出しに章番号を振る。

仕様の正本は `conventions/numbering-starts-at-one.md`「章番号」。
書式は Google Docs 用の章番号ツール（https://github.com/nnh/chapter_numbering）に
合わせてある。区切りと末尾がピリオド、番号と見出し文字列の間は半角スペース1つ。

  1. 見出し1
  1.1. 見出し2
  1.1.1. 見出し3

使い方:

  python3 number-headings.py <file.md> [...] [--top-level 2] [--dry-run]
  python3 number-headings.py <file.md> --remove

既定では `##` を第1レベルとする。md は文書題名を `#` 1つで持つため、
題名に番号を振らないための既定である。題名を持たない文書は --top-level 1 を渡す。

冪等——既にある番号は剥がして付け直すので、何度実行しても同じ結果になる。
コードブロック（``` で囲まれた範囲）の中は見出しとみなさない。

見出しの階層が飛んでいる文書（`##` の直下に `####` があるなど）は、
番号に 0 が現れる。0 を作らないのが規約なので、この場合は何も書かずに
該当行を報告して終わる。直すのは階層であって番号ではない。
"""

import argparse
import sys

DIGITS = "0123456789."


def split_heading(text):
    """見出し行を (井桁の数, 本文) に分ける。見出しでなければ None。"""
    hashes = 0
    while hashes < len(text) and text[hashes] == "#":
        hashes += 1
    if hashes == 0 or hashes > 6:
        return None
    if hashes >= len(text) or text[hashes] != " ":
        return None
    return hashes, text[hashes + 1:].strip()


def strip_number(title):
    """先頭が「数字とピリオドだけ」で末尾がピリオドのトークンなら剥がす。"""
    parts = title.split(" ", 1)
    if len(parts) != 2:
        return title
    head = parts[0]
    if head.endswith(".") and head.strip(DIGITS) == "":
        return parts[1].lstrip()
    return title


def process(path, top_level, remove, dry_run):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    counters = []
    in_fence = False
    changed = 0
    skips = []
    prev_depth = 0
    out = []

    for lineno, line in enumerate(lines, 1):
        body = line.rstrip()
        ending = line[len(body):]

        if body.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        parsed = split_heading(body)
        if parsed is None:
            out.append(line)
            continue

        hashes, title = parsed
        title = strip_number(title)

        if hashes < top_level:
            out.append(line)
            continue

        depth = hashes - top_level + 1

        if remove:
            new = ("#" * hashes) + " " + title
        else:
            if depth > prev_depth + 1 and prev_depth > 0:
                skips.append((lineno, body))
            prev_depth = depth
            while len(counters) < depth:
                counters.append(0)
            counters = counters[:depth]
            counters[depth - 1] += 1
            num = ".".join([str(c) for c in counters]) + "."
            new = ("#" * hashes) + " " + num + " " + title

        if new + ending != line:
            changed += 1
        out.append(new + ending)

    if skips:
        print("見出しの階層が飛んでいます。番号に 0 が出るため書き込みません: " + path)
        for lineno, body in skips:
            print("  " + str(lineno) + ": " + body)
        return None

    if dry_run:
        print(path + " (dry-run) " + str(changed) + " 行")
    else:
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(out)
        print(path + " " + str(changed) + " 行")
    return changed


def main():
    p = argparse.ArgumentParser(description="md の見出しに章番号を振る")
    p.add_argument("files", nargs="+")
    p.add_argument("--top-level", type=int, default=2,
                   help="第1レベルとする井桁の数（既定 2、つまり ##）")
    p.add_argument("--remove", action="store_true", help="番号を剥がす")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    failed = False
    for path in args.files:
        if process(path, args.top_level, args.remove, args.dry_run) is None:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
