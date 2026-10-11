#!/usr/bin/env python3
"""PreToolUse（Bash）：相手に届く送信を含むコマンドを、実行の直前にユーザーへ確認させる。

対象はメールの送信・カレンダー招待の通知・チャットへの投稿のように、実行した瞬間に相手へ
届いて取り消せない操作。コマンド文字列と、コマンドが実行するスクリプトファイルの中身を
検査し、該当すれば permissionDecision "ask" を返す。自動許可の設定や auto mode でも
確認の画面が出るので、送るかどうかはユーザーが決める。設計と経緯は hooks/README.md。
"""
import json
import os
import re
import shlex
import sys

PATTERNS = [
    (r"sendUpdates\W{0,6}(?:all|externalOnly)", "Google Calendar の招待・変更の通知（sendUpdates）"),
    (r"sendNotifications\W{0,6}[Tt]rue", "Google Calendar の招待の通知（sendNotifications）"),
    (r"/(?:messages|drafts)/send\b", "Gmail の送信"),
    (r"\b(?:messages|drafts)\(\)\s*\.send\(", "Gmail の送信"),
    (r"chat\.postMessage", "Slack への投稿"),
]
SCRIPT_EXT = (".py", ".mjs", ".js", ".cjs", ".ts", ".sh", ".rb")
MAX_BYTES = 1_000_000


def script_files(command, cwd):
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for t in tokens:
        if not t.endswith(SCRIPT_EXT):
            continue
        p = os.path.expanduser(t)
        if not os.path.isabs(p):
            p = os.path.join(cwd, p)
        if os.path.isfile(p):
            yield p


def findings(text):
    return sorted({label for pat, label in PATTERNS if re.search(pat, text)})


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()
    hits = []
    for label in findings(command):
        hits.append(f"{label}［コマンド］")
    for p in script_files(command, cwd):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_BYTES)
        except OSError:
            continue
        for label in findings(text):
            hits.append(f"{label}［{os.path.basename(p)}］")
    if not hits:
        return
    reason = (
        "相手に届く送信を含みます：" + "、".join(hits) + "。"
        "ユーザーが「送って」と明示していなければ許可しないでください。"
        "作るだけなら通知を止める指定（Calendar は sendUpdates=none）に替え、Gmail は下書きに留めます。"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
