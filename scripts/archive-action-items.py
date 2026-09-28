#!/usr/bin/env python3
"""完了した action item を隣の action-items-archive.md へ退避する。

仕様の正本は `conventions/action-items-convention.md`「完了項目のアーカイブ」。
このスクリプトはその実装で、規約が「退避は機械的に行う。判断は要らない」と
書いている部分だけを担う。何を完了とみなすかの判断は行わない。

  python3 archive-action-items.py [<action-items.md> ...] [--dry-run]
  引数なしなら cwd の action-items.md を対象にする（`$(find ...)` が空でも落ちない）。

単位は `## ` の節。`### ` 以下の小見出しは節の中の地の文として扱う。

移すもの:
  - `- [x]` のブロック（続く、より深いインデントの行を含む）。`[X]` も完了とみなす
  - 未完了の親の配下にある完了済みの子。左端に寄せて退避する
  - 未完了が1つも残らない節は、見出しと地の文ごと移る

残すもの:
  - `- [ ]` と `- [!]`（判断待ち）
  - **配下に未完了を持つ `- [x]`**。親が済んでいても子が残っているため
  - 最初の `## ` より前の前書き（タイトル・作成日・改訂日・正本へのリンク）
  - 地の文。ただし中身が退避されて空になった小見出しは消す

退避先に同名の節があればその末尾へ追記し、無ければ末尾に新しく作る。小見出しの下から
移した項目は、退避先でも同じ小見出しの下に置く。冪等——2回目以降は何も動かない。
"""

import argparse
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

# **`[x]` だけが完了で、`[ ]` と `[!]` はどちらも未完了。**
# `[ ]` だけを未完了として書くと `[!]` が地の文に見え、節の判定が「未完了ゼロ」と誤って
# 回答待ちの項目を archive へ流し込む（2026-08-12 に実際に起き、回答待ち3件が移った）。
ITEM_RE = re.compile(r"^- \[([ xX!])\] ")     # 項目行かどうか（マークを捕獲）
OPEN_RE = re.compile(r"^- \[[ !]\] ")         # 未完了の項目行かどうか
SUBHEAD_RE = re.compile(r"^#{3,6}\s")         # 節の中の小見出し


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def is_open(line):
    return bool(OPEN_RE.match(line.lstrip(" ")))


def parse_blocks(body):
    """行列を (kind, lines) のブロック列にする。kind は 'done' / 'open' / 'plain'。"""
    blocks = []
    i = 0
    while i < len(body):
        line = body[i]
        m = ITEM_RE.match(line.lstrip(" "))
        if not m:
            blocks.append(("plain", [line]))
            i += 1
            continue
        kind = "done" if m.group(1) in "xX" else "open"
        base = indent_of(line)
        chunk = [line]
        i += 1
        while i < len(body):
            nxt = body[i]
            if nxt.strip() == "":
                # 空行を挟んでもより深いインデントが続くなら同じ項目（コードブロック等）
                j = i
                while j < len(body) and body[j].strip() == "":
                    j += 1
                if j < len(body) and indent_of(body[j]) > base:
                    chunk.extend(body[i:j])
                    i = j
                    continue
                break
            if indent_of(nxt) > base:
                chunk.append(nxt)
                i += 1
                continue
            break
        blocks.append((kind, chunk))
    return blocks


def has_open(chunk):
    """配下に未完了を持つか。持つ親は完了扱いにできない。"""
    return any(is_open(x) for x in chunk)


def split_sections(lines):
    """(前書き, [(見出し行, 本文行), ...]) に分解する。"""
    first = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    header, secs, title, body = lines[:first], [], None, []
    for l in lines[first:]:
        if l.startswith("## "):
            if title is not None:
                secs.append((title, body))
            title, body = l, []
        else:
            body.append(l)
    if title is not None:
        secs.append((title, body))
    return header, secs


def trim(lines):
    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    return out


def dedent(chunk):
    """入れ子から取り出した完了ブロックを左端に寄せる（親を失って浮くのを防ぐ）。"""
    base = indent_of(chunk[0])
    if base == 0:
        return chunk
    return [l[base:] if l.startswith(" " * base) else l.lstrip(" ") for l in chunk]


def drop_empty_subheads(lines):
    """中身が退避されて、次の見出しまで空行しか残らない小見出しを消す。"""
    out = []
    for i, l in enumerate(lines):
        if SUBHEAD_RE.match(l):
            rest = lines[i + 1:]
            nxt = next((x for x in rest if x.strip()), None)
            if nxt is None or SUBHEAD_RE.match(nxt):
                continue
        out.append(l)
    return out


def split_section(body):
    """節の本文を (残す行, 退避する行) に分ける。"""
    live, arch = [], []
    sub, sub_out = None, False

    def to_arch(chunk):
        nonlocal sub_out
        if sub is not None and not sub_out:
            arch.extend(([""] if arch else []) + [sub, ""])
            sub_out = True
        arch.extend(chunk)

    for kind, chunk in parse_blocks(body):
        if kind == "plain" and SUBHEAD_RE.match(chunk[0]):
            sub, sub_out = chunk[0], False
            live.extend(chunk)
        elif kind == "done" and not has_open(chunk):
            to_arch(chunk)
        elif kind == "open":
            # 未完了ブロック配下の完了サブ項目も退避する
            keep = [chunk[0]]
            for k2, c2 in parse_blocks(chunk[1:]):
                if k2 == "done" and not has_open(c2):
                    to_arch(dedent(c2))
                else:
                    keep.extend(c2)
            live.extend(keep)
        else:
            live.extend(chunk)
    return live, arch


def join(lines):
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"


def process(src: Path, dry: bool) -> int:
    arch_path = src.with_name("action-items-archive.md")
    header, secs = split_sections(src.read_text(encoding="utf-8").split("\n"))
    live_secs, moved = [], []  # moved: [(見出し行, 退避する行)]

    for title, body in secs:
        live_lines, arch_lines = split_section(body)
        if not arch_lines:
            live_secs.append((title, body))
        elif any(is_open(l) for l in live_lines):
            moved.append((title, trim(arch_lines)))
            live_secs.append((title, trim(drop_empty_subheads(live_lines)) + [""]))
        else:
            # 未完了が1件も残らない節だけ、地の文ごと丸ごと移す。
            # `[!]` も未完了なので、ここへ来るのは全項目が `[x]` のときに限る。
            moved.append((title, trim(body)))

    if not moved:
        print(f"退避なし: {src}")
        return 0

    out = trim(header) + [""]
    for title, body in live_secs:
        out.append(title)
        out.extend(trim(body))
        out.append("")
    live_text = join(out)

    if arch_path.is_file():
        a_header, a_secs = split_sections(arch_path.read_text(encoding="utf-8").split("\n"))
    else:
        # 呼び出し元は `find . -maxdepth 2` の結果を渡すので path は `./action-items.md` の
        # 相対形になり、parent.name が空になる。プロジェクト名は resolve() 後の親から取る。
        # 見出しは名詞句のみ（md 見出し規約。括弧書きの副題を付けない）。
        project = src.resolve().parent.name
        a_header = [
            f"# {project} アクションアイテム 完了分",
            "",
            f"[{src.name}]({src.name}) から退避した完了項目。セッション終了時に "
            "`~/.claude/scripts/archive-action-items.py` が自動で移す。",
            "",
            "規約: `~/.claude/conventions/action-items-convention.md`",
        ]
        a_secs = []
    a_map = {t.strip(): i for i, (t, _) in enumerate(a_secs)}
    for title, arch_lines in moved:
        key = title.strip()
        if key in a_map:
            i = a_map[key]
            a_secs[i] = (a_secs[i][0], trim(a_secs[i][1]) + arch_lines)
        else:
            a_secs.append((title, arch_lines))
            a_map[key] = len(a_secs) - 1

    out = trim(a_header) + [""]
    for title, body in a_secs:
        out.append(title)
        out.extend(trim(body))
        out.append("")
    arch_text = join(out)

    n = sum(1 for _, b in moved for l in b if ITEM_RE.match(l.lstrip(" ")) and l.lstrip(" ")[3] in "xX")
    titles = "、".join(t[3:].strip() for t, _ in moved)
    if dry:
        print(f"（dry-run）{src}: {n} 件を退避します（{titles}）")
        return n
    src.write_text(live_text, encoding="utf-8")
    arch_path.write_text(arch_text, encoding="utf-8")
    print(f"{src}: {n} 件を {arch_path.name} へ退避しました（{titles}）")
    return n


def main() -> int:
    p = argparse.ArgumentParser(description="完了した action item を archive へ退避する")
    p.add_argument("paths", nargs="*", help="action-items.md のパス（省略時は cwd の action-items.md）")
    p.add_argument("--dry-run", action="store_true", help="何が移るかだけ表示する")
    a = p.parse_args()

    targets = a.paths or (["action-items.md"] if Path("action-items.md").is_file() else [])
    if not targets:
        print("退避対象なし")
        return 0
    rc = 0
    for raw in targets:
        path = Path(raw)
        if not path.is_file():
            print(f"見つかりません: {path}", file=sys.stderr)
            rc = 1
            continue
        process(path, a.dry_run)
    return rc


if __name__ == "__main__":
    sys.exit(main())
