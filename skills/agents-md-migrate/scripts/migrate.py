#!/usr/bin/env python3
"""プロジェクトの指示ファイルを CLAUDE.md から AGENTS.md へ移す。

AGENTS.md は Claude Code（2.1.277 以降）・Codex・Antigravity（agy）などが共通に読む名前。
CLAUDE.md と AGENTS.md が混ざると、Claude Code は既定で AGENTS.md を全部読まなくなる。

使い方:
  python3 migrate.py [--root <dir>] [--apply] [--push]
    --root   探す場所。既定は ~/Projects
    --apply  移す（既定は計画を出すだけ）
    --push   移したあと push する（上流の設定があるブランチだけ）

やること:
  - git で追跡している CLAUDE.md は git mv で AGENTS.md に改名し、そのリポジトリでコミットする。
    コミットには改名と書き換えたファイルだけを入れ、ほかの未コミットの変更は巻き込まない
  - git で追跡していない CLAUDE.md（git 管理外のフォルダ、未追跡のファイル）は名前だけ変える
  - md のリンクで、改名したファイルを指すものを直す。改名した指示ファイルの本文では、
    指示ファイルを指す「CLAUDE.md」を AGENTS.md に直す（グローバルの ~/.claude/CLAUDE.md は残す）
  - 上流より遅れているリポジトリは、--apply のとき先に pull --ff-only する。できなければ飛ばす

飛ばすもの（報告だけする）:
  - 同じフォルダに AGENTS.md もある（中身を1本にまとめるのは人か Claude が行う）
  - CLAUDE.md に未コミットの変更がある、シンボリックリンクである
  - 他者のリポジトリの写し（reference・legacy・vendor の下）と隠しフォルダ（.claude/worktrees など）
"""
import argparse
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
SKIP_DIR = {"node_modules", "reference", "legacy", "vendor", "__pycache__"}
MSG = "プロジェクトの指示ファイルを CLAUDE.md から AGENTS.md へ移す（Claude Code・Codex・agy が共通に読む名前）"
LINK = re.compile(r"(\[[^\]]*\]\()(<?)([^)>\s]*?CLAUDE\.md)(>?)(\)|#[^)]*\))")
PATHY = re.compile(r"(?<![\w./~-])((?:~|\.\.?|[\w.-]+)(?:/[\w.\-]+)*/)?CLAUDE\.md")


def git(repo, *a, check=False):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)} @ {repo}: {r.stderr.strip()}")
    return r


def toplevel(d):
    r = git(d, "rev-parse", "--show-toplevel")
    return os.path.normpath(r.stdout.strip()) if r.returncode == 0 else None


def find(root):
    """root の下の CLAUDE.md を、(リポジトリの最上位 or None, ファイルの絶対パス) で返す。"""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIR]
        if "CLAUDE.md" in files:
            out.append(os.path.join(d, "CLAUDE.md"))
    return out


def resolve(token_dir, base_dir, repo, root):
    if token_dir.startswith("~"):
        cands = [os.path.join(HOME, token_dir[2:] if token_dir.startswith("~/") else token_dir[1:])]
    else:
        cands = [os.path.join(base_dir, token_dir), os.path.join(repo or base_dir, token_dir), os.path.join(root, token_dir)]
    return [os.path.realpath(os.path.join(c, "CLAUDE.md")) for c in cands]


def rewrite_links(text, base_dir, repo, root, renamed):
    def rep(m):
        target = m.group(3)
        if re.match(r"[a-z]+://", target):
            return m.group(0)
        if any(c in renamed for c in resolve(os.path.dirname(target), base_dir, repo, root)):
            return m.group(1) + m.group(2) + target[: -len("CLAUDE.md")] + "AGENTS.md" + m.group(4) + m.group(5)
        return m.group(0)
    text = LINK.sub(rep, text)
    return re.sub(r"\[([^\]]*?)CLAUDE\.md([^\]]*?)\]\(([^)]*?)AGENTS\.md", r"[\1AGENTS.md\2](\3AGENTS.md", text)


def rewrite_prose(text, base_dir, repo, root, renamed):
    out = []
    for line in text.split("\n"):
        def rep(m):
            pre = line[max(0, m.start() - 14):m.start()]
            whole = m.group(0)
            if re.search(r"(global|グローバル)", pre, re.I) or ".claude/" in whole:
                return whole
            d = (m.group(1) or "").rstrip("/")
            if not d:
                return "AGENTS.md"
            return whole[: -len("CLAUDE.md")] + "AGENTS.md" if any(c in renamed for c in resolve(d, base_dir, repo, root)) else whole
        out.append(PATHY.sub(rep, line))
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description="CLAUDE.md を AGENTS.md へ移す")
    p.add_argument("--root", default=os.path.join(HOME, "Projects"))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--push", action="store_true")
    a = p.parse_args()
    root = os.path.realpath(os.path.expanduser(a.root))
    if not os.path.isdir(root):
        print(f"探す場所がありません: {root}")
        return 2

    # リポジトリごとに分ける。遅れているリポジトリは先に追随する
    by_repo, plain = {}, []
    for f in find(root):
        top = toplevel(os.path.dirname(f))
        rel = os.path.relpath(f, top).replace(os.sep, "/") if top else None
        tracked = bool(top) and git(top, "ls-files", "--error-unmatch", rel).returncode == 0
        if tracked:
            by_repo.setdefault(top, []).append(rel)
        else:
            plain.append(f)
    for repo in list(by_repo):
        git(repo, "fetch", "-q")
        st = git(repo, "status", "-sb").stdout.split("\n")[0]
        if "behind" in st:
            if a.apply and git(repo, "pull", "-q", "--ff-only").returncode == 0:
                print(f"pull した：{repo}")
            else:
                print(f"飛ばす（上流より遅れている{'・pull できない' if a.apply else '。--apply で先に pull する'}）：{repo}")
                if a.apply:
                    by_repo.pop(repo)

    moves, skips = {}, []
    for repo, fs in by_repo.items():
        for rel in fs:
            full = os.path.join(repo, rel)
            if os.path.exists(os.path.join(os.path.dirname(full), "AGENTS.md")):
                skips.append((full, "同じフォルダに AGENTS.md もある。中身を1本にまとめてから CLAUDE.md を消す"))
            elif os.path.islink(full):
                skips.append((full, "シンボリックリンク"))
            elif git(repo, "status", "--porcelain", "--", rel).stdout.strip():
                skips.append((full, "未コミットの変更がある。コミットしてから再実行する"))
            else:
                moves.setdefault(repo, []).append(rel)
    plain_ok = []
    for f in plain:
        if os.path.exists(os.path.join(os.path.dirname(f), "AGENTS.md")):
            skips.append((f, "同じフォルダに AGENTS.md もある。中身を1本にまとめてから CLAUDE.md を消す"))
        elif os.path.islink(f):
            skips.append((f, "シンボリックリンク"))
        else:
            plain_ok.append(f)
    renamed = {os.path.realpath(os.path.join(r, f)) for r, fs in moves.items() for f in fs} | {os.path.realpath(f) for f in plain_ok}

    print(f"{'移す' if a.apply else '移す予定'}：git の追跡 {sum(len(v) for v in moves.values())} 件（{len(moves)} リポジトリ）・git の外 {len(plain_ok)} 件")
    for f, why in skips:
        print(f"飛ばす：{f}（{why}）")

    for repo, fs in moves.items():
        edits = []
        for rel in git(repo, "ls-files", "-z", "*.md").stdout.split("\0"):
            if not rel or any(part in SKIP_DIR for part in rel.split("/")):
                continue
            path = os.path.join(repo, rel)
            is_instr = rel in fs
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if not is_instr and git(repo, "status", "--porcelain", "--", rel).stdout.strip():
                continue
            text = open(path, encoding="utf-8").read()
            if "CLAUDE.md" not in text:
                continue
            new = rewrite_links(text, os.path.dirname(path), repo, root, renamed)
            if is_instr:
                new = rewrite_prose(new, os.path.dirname(path), repo, root, renamed)
            if new != text:
                edits.append((rel, new))
        print(f"\n== {repo}：改名 {len(fs)}・書き換え {len(edits)}")
        for rel in fs:
            print(f"  {rel} → AGENTS.md")
        for rel, _ in edits:
            print(f"  書き換え：{rel}")
        if not a.apply:
            continue
        for rel, new in edits:
            with open(os.path.join(repo, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
        news = []
        for rel in fs:
            new_rel = (os.path.dirname(rel) + "/AGENTS.md").lstrip("/")
            git(repo, "mv", rel, new_rel, check=True)
            news.append(new_rel)
        paths = sorted(set(fs) | set(news) | {r if r not in fs else (os.path.dirname(r) + "/AGENTS.md").lstrip("/") for r, _ in edits})
        git(repo, "add", "--", *[x for x in paths if os.path.exists(os.path.join(repo, x))], check=True)
        git(repo, "commit", "-q", "-m", MSG, "--", *paths, check=True)
        print(f"  コミット：{git(repo, 'log', '--oneline', '-1').stdout.strip()}")
        if a.push:
            if git(repo, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
                print("  push：上流の設定が無いので飛ばした")
            else:
                r = git(repo, "push", "-q")
                print("  push：" + ("済み" if r.returncode == 0 else "失敗 " + r.stderr.strip()[:200]))

    if plain_ok:
        print("\n== git の外")
        for f in plain_ok:
            print(f"  {f} → AGENTS.md")
            if a.apply:
                os.rename(f, os.path.join(os.path.dirname(f), "AGENTS.md"))
    if not a.apply:
        print("\n計画だけを出した。移すには --apply を付けて再実行する")
    return 0


if __name__ == "__main__":
    sys.exit(main())
