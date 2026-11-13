#!/usr/bin/env python3
"""Codex CLI の PreToolUse：Claude Code の検査フック（~/.claude/hooks/*-guard.py）を Codex でも効かせる。

どの検査をどのツールに掛けるかは ~/.claude/settings.json の PreToolUse が正本で、ここには
写さない。matcher が Codex のツール名に当てはまり、コマンドが ~/.claude/hooks/ の *-guard.py
を呼ぶ登録だけを、同じ標準入力で実行する（Orca など検査以外のフックは呼ばない）。Codex は
ツール名を Claude と同じ形（シェルは Bash、MCP は mcp__<server>__<tool>）で渡すので、
検査の側は手を入れずに動く。

Codex の PreToolUse は permissionDecision "ask" を扱えず、フックの失敗として記録したうえで
ツールを実行してしまう（https://learn.chatgpt.com/docs/hooks）。そのため ask は deny に
読み替える。Codex は確認の画面を出せないので、送る・消すと決めたら人が自分で実行する。
設計と経緯は hooks/README.md「Codex CLI での検査」。
"""
import json
import os
import re
import subprocess
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
GUARD = re.compile(r"\.claude/hooks/[A-Za-z0-9_.-]*-guard\.py")


def matches(matcher, tool):
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def guard_commands(tool):
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            groups = json.load(f).get("hooks", {}).get("PreToolUse", [])
    except (OSError, ValueError):
        return []
    out = []
    for g in groups:
        if not matches(g.get("matcher"), tool):
            continue
        for h in g.get("hooks", []):
            c = h.get("command") or ""
            if h.get("type", "command") == "command" and GUARD.search(c) and c not in out:
                out.append(c)
    return out


def main():
    raw = sys.stdin.buffer.read()
    try:
        tool = json.loads(raw).get("tool_name") or ""
    except ValueError:
        return
    for cmd in guard_commands(tool):
        r = subprocess.run(["/bin/sh", "-c", cmd], input=raw, capture_output=True, timeout=30)
        name = GUARD.search(cmd).group(0).rsplit("/", 1)[1]
        if r.returncode == 2:
            reason = r.stderr.decode("utf-8", "replace").strip() or name + " が止めた"
            return deny(reason, name)
        try:
            spec = json.loads(r.stdout.decode("utf-8", "replace") or "{}").get("hookSpecificOutput") or {}
        except ValueError:
            continue
        decision = spec.get("permissionDecision")
        if decision == "deny":
            return deny(spec.get("permissionDecisionReason", ""), name)
        if decision == "ask":
            return deny(spec.get("permissionDecisionReason", "")
                        + "（Codex は確認の画面を出せないため止めた。実行するならユーザーが自分で行う）", name)


def deny(reason, name):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[%s] %s" % (name, reason),
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
