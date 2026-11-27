#!/usr/bin/env python3
"""AI 操作用の常駐 Chrome（CDP）に、このセッションが開いたまま残したタブを Stop で検出する。

instructions/tools.md「ブラウザのタブの後始末」を機械で支える歯止め。自動では閉じない
（人に見せるために開いたタブを巻き込まないため）。閉じるかどうかは Claude が判断する。

- 対象のブラウザは、起動中のプロセスの引数から引く。`--remote-debugging-port=<N>` と、
  `--user-data-dir` に CLAUDE_CHROME_DIR_MATCH（既定 `claude-chrome`）を含む Chrome。ポートと
  プロファイルの一覧はこのスクリプトに持たない
- PreToolUse（Bash）：コマンドが AI 用 Chrome を触る形跡（claude-chrome・connectOverCDP・
  remote-debugging-port・/json/new・Target.createTarget・対象のポート番号）を持つときだけ、
  その時点のタブの targetId を記録する
- PostToolUse／PostToolUseFailure：その呼び出しのあいだに現れたタブを「このセッションが開いたタブ」に足す
- Stop：このセッションが開いたタブのうち、まだ残っているものだけを数える。ほかのセッションや人が
  開いたタブ、hook の出力・CLAUDE.md・会話の文面は判定に使わない
- 残りがあれば decision "block" で Claude に閉じさせる。返答が隠れないよう、閉じた後に直前の
  返答を載せ直すよう求める。同じタブの組には1回しか言わず、再入（stop_hook_active）では止めない
- SessionStart など上記以外のイベントでは何もしない（旧版の登録が残っていても害が無い）
"""
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request

DIR_MATCH = os.environ.get("CLAUDE_CHROME_DIR_MATCH", "claude-chrome")
STATE_DIR = os.environ.get("TMPDIR", "/tmp")
TRACE = re.compile(r"claude-chrome|connectOverCDP|remote-debugging-port|/json/new|Target\.createTarget")


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


def snapshot(ports=None):
    snap = {}
    for p in chrome_ports() if ports is None else ports:
        ts = pages(p)
        if ts is not None:
            snap[str(p)] = [t["id"] for t in ts]
    return snap


def safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))


def state_path(sid, kind, extra=""):
    return os.path.join(STATE_DIR, f"browser-tab-check-{kind}-{safe(sid)}{('-' + safe(extra)) if extra else ''}.json")


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


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def touches_browser(tool_input):
    text = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
    if TRACE.search(text):
        return True
    return any(re.search(rf":{p}(?!\d)", text) for p in chrome_ports())


def attribute(sid, pre_path):
    """pre の記録から今までに現れたタブを、このセッションのものとして足す。"""
    before = load(pre_path)
    remove(pre_path)
    if before is None:
        return
    now = snapshot()
    mine = load(state_path(sid, "mine")) or {}
    for p, ids in now.items():
        new = [i for i in ids if i not in before.get(p, [])]
        if new:
            mine[p] = sorted(set(mine.get(p, [])) | set(new))
    save(state_path(sid, "mine"), mine)


def on_stop(data, sid):
    for pre in glob.glob(os.path.join(STATE_DIR, f"browser-tab-check-pre-{safe(sid)}-*.json")):
        attribute(sid, pre)  # PostToolUse が来なかった呼び出し（中断など）の分
    mine = load(state_path(sid, "mine")) or {}
    if not mine:
        return
    left, alive = [], {}
    for p, ids in mine.items():
        ts = pages(int(p)) or []
        still = [t for t in ts if t["id"] in ids]
        if still:
            alive[p] = [t["id"] for t in still]
            left += [(p, t) for t in still]
    save(state_path(sid, "mine"), alive)
    if not left:
        return
    key = sorted(t["id"] for _, t in left)
    if load(state_path(sid, "told")) == key or data.get("stop_hook_active"):
        return
    save(state_path(sid, "told"), key)
    lines = [f"  ポート {p}：{(t.get('title') or '')[:40]}　{(t.get('url') or '')[:80]}　(id {t['id']})" for p, t in left]
    reason = (
        "このセッションのツール呼び出しで AI 操作用の Chrome に開いたタブが残っています：\n" + "\n".join(lines)
        + "\ntools.md「ブラウザのタブの後始末」に従い、作業が終わったタブは閉じる"
        "（CDP の /json/close/<id>、または Target.closeTarget）。人に見せるために開いたタブは残してよい。"
        "閉じた結果そのブラウザにタブが1つも残らなければ、アプリも終了する。"
        "ユーザーには最後の発言しか見えないことがあるため、後始末の後の最後の発言には、"
        "直前の返答をそのまま載せ直し、タブの扱いは末尾に一言だけ添える（同じ組では再度は言わない）。"
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    sid = data.get("session_id", "x")
    event = data.get("hook_event_name", "")
    if event == "PreToolUse":
        if data.get("tool_name") == "Bash" and touches_browser(data.get("tool_input") or {}):
            save(state_path(sid, "pre", data.get("tool_use_id", "x")), snapshot())
    elif event in ("PostToolUse", "PostToolUseFailure"):
        pre = state_path(sid, "pre", data.get("tool_use_id", "x"))
        if os.path.exists(pre):
            attribute(sid, pre)
    elif event == "Stop":
        on_stop(data, sid)


if __name__ == "__main__":
    main()
