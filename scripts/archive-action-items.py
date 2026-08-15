#!/usr/bin/env python3
"""完了した action item を隣の action-items-archive.md へ退避する。

仕様の正本は `conventions/action-items-convention.md`「完了項目のアーカイブ」。
このスクリプトはその実装で、規約が「退避は機械的に行う。判断は要らない」と
書いている部分だけを担う。何を完了とみなすかの判断は行わない。

  python3 archive-action-items.py <action-items.md> [...] [--dry-run]

移すもの:
  - `- [x]` のブロック（続く、より深いインデントの行を含む）
  - 未完了が1つも残らないセクションは、見出しと地の文ごと移る

残すもの:
  - `- [ ]` と `- [!]`（判断待ち）
  - **配下に未完了を持つ `- [x]`**。親が済んでいても子が残っているため
  - 見出しより前の前書き（タイトル・作成日・改訂日・正本へのリンク）

退避先に同名の見出しがあればその末尾へ追記し、無ければ末尾に新しく作る。
冪等——2回目以降は何も動かない。
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

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
ITEM = re.compile(r"^(\s*)- \[(.)\]\s")
DONE = "x"


class Block:
    """1つの箇条書き項目と、それにぶら下がる行。"""

    def __init__(self, lines, mark, indent):
        self.lines = lines
        self.mark = mark
        self.indent = indent

    @property
    def has_open_child(self) -> bool:
        """配下に未完了があるか。先頭行（自分自身）は見ない。"""
        for line in self.lines[1:]:
            m = ITEM.match(line)
            if m and m.group(2).lower() != DONE:
                return True
        return False

    @property
    def done(self) -> bool:
        return self.mark.lower() == DONE and not self.has_open_child


class Section:
    """見出し1つと、その配下の行。子セクションは持たせず平坦に扱う。"""

    def __init__(self, level, title, heading_line):
        self.level = level
        self.title = title
        self.heading_line = heading_line
        self.body = []          # 見出し直後の地の文（箇条書き以外）
        self.blocks = []        # 箇条書き項目

    @property
    def has_open(self) -> bool:
        return any(not b.done for b in self.blocks)


def parse(lines):
    """前書き・セクションの並びへ分ける。"""
    preamble = []
    sections = []
    cur = None

    def flush_line(line):
        # 箇条書きの継続行（より深いインデント、または空行に続く同一項目）を
        # 直前のブロックへ寄せる。トップレベルの項目だけを Block の起点にする。
        m = ITEM.match(line)
        if m and len(m.group(1)) == 0:
            cur.blocks.append(Block([line], m.group(2), 0))
            return
        if cur.blocks and (line.strip() == "" or line.startswith((" ", "\t", "  -"))):
            cur.blocks[-1].lines.append(line)
            return
        if cur.blocks and line.strip() and not line.startswith("#"):
            # 箇条書きが始まったあとの地の文は、直前の項目の続きとして扱う
            cur.blocks[-1].lines.append(line)
            return
        cur.body.append(line)

    for line in lines:
        m = HEADING.match(line)
        if m:
            cur = Section(len(m.group(1)), m.group(2).strip(), line)
            sections.append(cur)
            continue
        if cur is None:
            preamble.append(line)
        else:
            flush_line(line)
    return preamble, sections


def _trim(lines):
    """末尾の空行を落とす。"""
    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    return out


def split_section(sec):
    """(残す行, 退避する行) を返す。"""
    keep_blocks = [b for b in sec.blocks if not b.done]
    move_blocks = [b for b in sec.blocks if b.done]
    if not move_blocks:
        return None, None

    moved = []
    if not keep_blocks:
        # 未完了が残らないので、見出しと地の文ごと移す
        moved = [sec.heading_line] + _trim(sec.body)
        if len(moved) > 1:
            moved.append("")  # 地の文と箇条書きの間の空行を保つ
        for b in move_blocks:
            moved += _trim(b.lines)
        return [], moved

    moved = [sec.heading_line]
    for b in move_blocks:
        moved += _trim(b.lines)
    kept = [sec.heading_line] + sec.body
    for b in keep_blocks:
        kept += b.lines
    return kept, moved


def merge_into_archive(archive_lines, moved_sections):
    """退避先へ差し込む。同名の見出しがあればその末尾へ、無ければ末尾に作る。"""
    out = list(archive_lines)
    for title, level, body in moved_sections:
        idx = None
        for i, line in enumerate(out):
            m = HEADING.match(line)
            if m and m.group(2).strip() == title and len(m.group(1)) == level:
                idx = i
                break
        if idx is None:
            if out and out[-1].strip() != "":
                out.append("")
            out.append("#" * level + " " + title)
            if body and body[0].strip() != "":
                out.append("")  # 見出しの直後に空行を置く
            out += body
            continue
        # 同名セクションの末尾（次の同レベル以上の見出しの直前）を探す
        end = len(out)
        for j in range(idx + 1, len(out)):
            m = HEADING.match(out[j])
            if m and len(m.group(1)) <= level:
                end = j
                break
        tail = end
        while tail > idx + 1 and out[tail - 1].strip() == "":
            tail -= 1
        out = out[:tail] + body + out[tail:]
    return out


def process(path: Path, dry: bool) -> int:
    archive_path = path.with_name("action-items-archive.md")
    lines = path.read_text(encoding="utf-8").splitlines()
    preamble, sections = parse(lines)

    kept_out = list(preamble)
    moved_sections = []
    moved_count = 0
    for sec in sections:
        kept, moved = split_section(sec)
        if moved is None:
            kept_out += [sec.heading_line] + sec.body
            for b in sec.blocks:
                kept_out += b.lines
            continue
        moved_count += sum(1 for b in sec.blocks if b.done)
        body = _trim(moved[1:])
        moved_sections.append((sec.title, sec.level, body))
        if kept:
            kept_out += kept

    if not moved_count:
        print(f"退避なし: {path}")
        return 0

    if archive_path.is_file():
        archive_lines = archive_path.read_text(encoding="utf-8").splitlines()
    else:
        archive_lines = [f"# {path.stem} 完了分", ""]
    archive_out = merge_into_archive(archive_lines, moved_sections)

    titles = "、".join(t for t, _, _ in moved_sections)
    if dry:
        print(f"（dry-run）{path}: {moved_count} 件を退避します（{titles}）")
        return 0

    path.write_text("\n".join(_trim(kept_out)) + "\n", encoding="utf-8")
    archive_path.write_text("\n".join(_trim(archive_out)) + "\n", encoding="utf-8")
    print(f"{path}: {moved_count} 件を {archive_path.name} へ退避しました（{titles}）")
    return moved_count


def main() -> int:
    p = argparse.ArgumentParser(description="完了した action item を archive へ退避する")
    p.add_argument("paths", nargs="+", help="action-items.md のパス")
    p.add_argument("--dry-run", action="store_true", help="何が移るかだけ表示する")
    a = p.parse_args()

    rc = 0
    for raw in a.paths:
        path = Path(raw)
        if not path.is_file():
            print(f"見つかりません: {path}", file=sys.stderr)
            rc = 1
            continue
        process(path, a.dry_run)
    return rc


if __name__ == "__main__":
    sys.exit(main())
