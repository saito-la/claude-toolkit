#!/usr/bin/env python3
"""orca-register-projects.py — ~/Projects のリポジトリを、この Mac の Orca に登録する。

  python3 orca-register-projects.py            # 未登録のものを登録する
  python3 orca-register-projects.py --dry-run  # 登録するものを表示するだけ
  <一覧を出すコマンド> | python3 orca-register-projects.py --stdin

登録する一覧は、既定では pj の索引（skills/pj の pj-recommend.py list。CLAUDE.md のある
フォルダ）を git リポジトリの最上位にまとめたもの。別の一覧を使うなら --stdin で
~/Projects からの相対パスを1行1件で渡す。この Mac に clone されていないもの、登録済みの
ものは飛ばすので、何度実行してもよい。

Orca の画面やサーバーが動いていない Mac では何もしない。Orca を起動しないためで、
次に動いているときの実行で登録される。Orca に登録を外すコマンドは無く、外すときは
Orca の画面で行う。入れ方と設定は同じフォルダの README.md。
"""
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Projects")
RECOMMEND = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "skills", "pj", "scripts", "pj-recommend.py")
# brew の cask は /Applications、管理者権限の無い端末（orca-install-user.sh）は ~/Applications
ORCA_FALLBACKS = [d + "/Orca.app/Contents/Resources/bin/orca" for d in ("/Applications", os.path.join(HOME, "Applications"))]


def find_orca():
    path = shutil.which("orca")
    if path:
        return path
    return next((p for p in ORCA_FALLBACKS if os.access(p, os.X_OK)), None)


def orca_json(orca, *args):
    out = subprocess.run([orca, *args, "--json"], capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    return data if data.get("ok") else None


def default_targets():
    """pj の索引にあるフォルダを、属する git リポジトリの最上位にまとめる。"""
    rels = subprocess.run([sys.executable, RECOMMEND, "list"],
                          capture_output=True, text=True, check=True).stdout.split("\n")
    roots = []
    for rel in filter(None, (r.strip() for r in rels)):
        out = subprocess.run(["git", "-C", os.path.join(PROJECTS, rel), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        top = out.stdout.strip()
        if out.returncode or not top.startswith(PROJECTS + os.sep) or "/.claude/worktrees/" in top:
            continue
        r = os.path.relpath(top, PROJECTS)
        if r not in roots:
            roots.append(r)
    return roots


def main(argv):
    dry = "--dry-run" in argv
    orca = find_orca()
    if not orca:
        print("orca-register: Orca が入っていないので何もしない")
        return 0
    status = orca_json(orca, "status")
    runtime = (status or {}).get("result", {}).get("runtime", {})
    if not runtime.get("reachable"):
        print("orca-register: Orca が動いていないので何もしない")
        return 0

    repos = orca_json(orca, "repo", "list")
    if repos is None:
        print("orca-register: 登録済みの一覧を取れなかった", file=sys.stderr)
        return 1
    registered = {os.path.realpath(r["path"]) for r in repos["result"]["repos"]}

    if "--stdin" in argv:
        targets = sys.stdin.read().split()
    else:
        targets = default_targets()
    added, absent, failed = [], [], []
    for rel in targets:
        full = os.path.join(PROJECTS, rel)
        if not os.path.exists(os.path.join(full, ".git")):
            absent.append(rel)
            continue
        if os.path.realpath(full) in registered:
            continue
        if dry:
            added.append(rel)
            continue
        if orca_json(orca, "repo", "add", "--path", full) is None:
            failed.append(rel)
        else:
            added.append(rel)

    verb = "登録する" if dry else "登録した"
    print(f"orca-register: 対象 {len(targets)} 件、{verb} {len(added)} 件、"
          f"未 clone {len(absent)} 件、失敗 {len(failed)} 件")
    for rel in added:
        print(f"  + {rel}")
    for rel in failed:
        print(f"  ! {rel}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
