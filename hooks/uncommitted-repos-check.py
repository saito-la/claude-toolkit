#!/usr/bin/env python3
"""セッションが触れたリポジトリの未コミット・未 push を Stop で検査する。

SESSION-END.md の Step 5 を機械で支える歯止め。cwd のリポジトリだけでなく、セッション中に
Write・Edit したファイルの属するリポジトリをすべて見る（別プロジェクトへ書いた成果物が
未追跡のまま残る事故への対策。2026-11-11 の岡田先生向けデッキ）。

- 直近のユーザー発言が「終了・終わり・終え」を含むとき：未コミット・未 push があれば
  decision "block" を返し、commit & push させる（stop_hook_active の再入では止めない）
- それ以外：警告（systemMessage）を出すだけで止めない。同じセッションでは30分に1回まで
- 何も無ければ無言。fetch はしない（遅くなるため）。未 push は upstream か origin/main との差で数える
"""
import json
import os
import re
import subprocess
import sys
import time

END = re.compile(r"終了|終わり|終わる|終え|セッションを閉じ|end session", re.I)
WARN_INTERVAL = 1800


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def toplevel(path):
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d) and d != "/":
        d = os.path.dirname(d)
    rc, out = git(d, "rev-parse", "--show-toplevel")
    return out.strip() if rc == 0 and out.strip() else None


def last_user_text(events):
    last = ""
    for e in events:
        if e.get("type") != "user":
            continue
        c = e.get("message", {}).get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if x.get("type") == "text")
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
            last = c
    return last


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tx = data.get("transcript_path") or ""
    events = []
    if tx and os.path.exists(tx):
        for line in open(tx, encoding="utf-8", errors="replace"):
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    paths = [data.get("cwd") or os.getcwd()]
    for e in events:
        if e.get("type") != "assistant":
            continue
        for x in e.get("message", {}).get("content", []) or []:
            if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name") in ("Write", "Edit"):
                fp = x.get("input", {}).get("file_path")
                if fp:
                    paths.append(fp)
    repos = sorted({r for r in (toplevel(p) for p in set(paths)) if r})
    problems = []
    for r in repos:
        rc, st = git(r, "status", "--porcelain")
        dirty = [l for l in st.splitlines() if l.strip()] if rc == 0 else []
        rc1, up = git(r, "rev-parse", "--abbrev-ref", "@{u}")
        base = up.strip() if rc1 == 0 and up.strip() else "origin/main"
        rc2, n = git(r, "rev-list", "--count", f"{base}..HEAD")
        ahead = int(n.strip()) if rc2 == 0 and n.strip().isdigit() else 0
        if dirty or ahead:
            problems.append(f"{r}：未コミット {len(dirty)} 件、未 push {ahead} コミット")
    if not problems:
        return
    msg = "未コミット・未 push のリポジトリがあります：\n" + "\n".join(problems)
    ending = bool(END.search(last_user_text(events)))
    if ending and not data.get("stop_hook_active"):
        print(json.dumps({"decision": "block", "reason": msg + "\nSESSION-END.md の Step 5 に従い、すべて commit & push してから終えること（確認は取らない）。"}, ensure_ascii=False))
        return
    sid = data.get("session_id", "x")
    stamp = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"uncommitted-repos-check-{sid}")
    try:
        if time.time() - os.path.getmtime(stamp) < WARN_INTERVAL:
            return
    except OSError:
        pass
    try:
        open(stamp, "w").close()
    except OSError:
        pass
    print(json.dumps({"systemMessage": msg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
