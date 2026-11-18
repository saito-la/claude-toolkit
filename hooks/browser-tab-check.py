#!/usr/bin/env python3
"""AI 操作用の常駐 Chrome（CDP）に、セッションが開いたまま残したタブを Stop で検出する。

instructions/tools.md「ブラウザのタブの後始末」を機械で支える歯止め。自動では閉じない
（人に見せるために開いたタブ・ほかのセッションのタブを巻き込まないため）。閉じるかどうかは Claude が判断する。

- 対象のブラウザは、起動中のプロセスの引数から引く。`--remote-debugging-port=<N>` と、
  `--user-data-dir` に CLAUDE_CHROME_DIR_MATCH（既定 `claude-chrome`）を含む Chrome。ポートと
  プロファイルの一覧はこのスクリプトに持たない
- SessionStart：その時点のタブの targetId をセッションごとに記録する（基準）
- Stop：基準に無いタブ（開始後に現れたタブ）を数える。基準が無いときは全タブが対象。
  このセッションが AI 用 Chrome を使った形跡（記録に claude-chrome・connectOverCDP・
  remote-debugging-port・対象のポート）が無ければ何もしない
- 残りがあれば decision "block" で Claude に閉じさせる。同じタブの組には1回しか言わない
  （再入の stop_hook_active でも止めない）。何も無ければ無言
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

DIR_MATCH = os.environ.get("CLAUDE_CHROME_DIR_MATCH", "claude-chrome")
STATE_DIR = os.environ.get("TMPDIR", "/tmp")
TRACE = re.compile(r"claude-chrome|connectOverCDP|remote-debugging-port")


def chrome_ports():
    try:
        out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    ports = set()
    for line in out.splitlines():
        m = re.search(r"--remote-debugging-port=(\d+)", line)
        d = re.search(r"--user-data-dir=(\S+)", line)
        if m and d and DIR_MATCH in d.group(1):
            ports.add(int(m.group(1)))
    return sorted(ports)


def pages(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]
    except Exception:
        return None


def state_path(sid, kind):
    return os.path.join(STATE_DIR, f"browser-tab-check-{kind}-{sid}.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    except OSError:
        pass


def snapshot():
    snap = {}
    for p in chrome_ports():
        ts = pages(p)
        if ts is not None:
            snap[str(p)] = [t["id"] for t in ts]
    return snap


def used_browser(transcript, ports):
    if not transcript or not os.path.exists(transcript):
        return False
    needles = [f":{p}" for p in ports]
    for line in open(transcript, encoding="utf-8", errors="replace"):
        if TRACE.search(line) or any(n in line for n in needles):
            return True
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    sid = data.get("session_id", "x")
    event = data.get("hook_event_name", "")
    if event == "SessionStart":
        save(state_path(sid, "base"), snapshot())
        return

    ports = chrome_ports()
    if not ports or not used_browser(data.get("transcript_path"), ports):
        return
    base = load(state_path(sid, "base")) or {}
    left = []
    for p in ports:
        ts = pages(p)
        for t in ts or []:
            if t["id"] not in base.get(str(p), []):
                left.append((p, t))
    if not left:
        return
    key = sorted(t["id"] for _, t in left)
    told = load(state_path(sid, "told"))
    if told == key:
        return
    save(state_path(sid, "told"), key)
    lines = [f"  ポート {p}：{(t.get('title') or '')[:40]}　{(t.get('url') or '')[:80]}　(id {t['id']})" for p, t in left]
    reason = (
        "AI 操作用の Chrome に、このセッションの開始後に現れたタブが残っています：\n" + "\n".join(lines)
        + "\ntools.md「ブラウザのタブの後始末」に従うこと。自分が開いて作業が終わったタブは閉じる"
        "（CDP の /json/close/<id>、または Target.closeTarget）。人に見せるために開いたタブ・ほかのセッションのタブは閉じない。"
        "閉じた結果そのブラウザにタブが1つも残らなければ、アプリも終了する。"
        "残すタブがあれば、理由を一言添えて終えてよい（同じ組では再度は言わない）。"
    )
    if data.get("stop_hook_active"):
        print(json.dumps({"systemMessage": reason}, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
