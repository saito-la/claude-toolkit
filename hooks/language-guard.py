#!/usr/bin/env python3
"""応答の言語を日本語に保つ。instructions/language.md を機械で守らせる。

- SessionStart（matcher: compact）：language.md を標準出力に出し、要約の直後の文脈へ規則を
  入れ直す。要約は英語で書かれ、要約後の文脈は英語が大半になるため、規則が埋もれる
- Stop：そのターンの最終応答が英語なら
  decision "block" を返し、日本語で書き直させる。書き直しの後（stop_hook_active）は止めない

ユーザーがそのターンの依頼で英語を指定したとき（「英語で」「in English」等）は検査しない。
コードブロック・インラインコード・引用（> の行）・URL・パスは数えない。ツール呼び出しの間の
文は見ない（書き直させても見える文は戻らず、最終応答を出し直させれば足りる）。設計と経緯は hooks/README.md。
"""
import json
import os
import re
import sys

LANG_MD = os.path.expanduser("~/.claude/instructions/language.md")
MIN_LETTERS = 40      # これより短い文は判定しない（「OK」等）
MIN_JA_RATIO = 0.25   # 和文字 /（和文字＋英字）がこれ未満なら英語とみなす
EN_REQUEST = re.compile(r"英語で|英文で|英訳|in English|English only", re.I)

JA = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
LATIN = re.compile(r"[A-Za-z]")
STRIP = [
    re.compile(r"```.*?```", re.S),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"^\s*>.*$", re.M),
    re.compile(r"https?://\S+"),
    re.compile(r"(?:~|\.{1,2})?/[\w.\-/@~]+"),
]


def visible(text):
    for p in STRIP:
        text = p.sub(" ", text)
    return text


def is_english(text):
    t = visible(text)
    ja = len(JA.findall(t))
    en = len(LATIN.findall(t))
    if ja + en < MIN_LETTERS:
        return False
    return ja / (ja + en) < MIN_JA_RATIO


def is_user_prompt(rec):
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
        return False
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return True
    if isinstance(c, list):
        return any(isinstance(x, dict) and x.get("type") == "text" for x in c) and not any(
            isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
    return False


def prompt_text(rec):
    c = rec["message"]["content"]
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))


def this_turn(path):
    """直近のユーザーの依頼の文と、その後にアシスタントが書いた文を返す。"""
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        return "", []
    start = None
    for i in range(len(recs) - 1, -1, -1):
        if is_user_prompt(recs[i]):
            start = i
            break
    if start is None:
        return "", []
    texts = []
    for r in recs[start + 1:]:
        if r.get("type") != "assistant":
            continue
        for x in (r.get("message") or {}).get("content") or []:
            if isinstance(x, dict) and x.get("type") == "text" and x.get("text", "").strip():
                texts.append(x["text"])
    return prompt_text(recs[start]), texts


def on_stop(data):
    if data.get("stop_hook_active"):
        return
    prompt, texts = this_turn(data.get("transcript_path", ""))
    last = data.get("last_assistant_message")
    if isinstance(last, str) and last.strip() and (not texts or texts[-1] != last):
        texts.append(last)
    if not texts or EN_REQUEST.search(prompt):
        return
    bad = [texts[-1]] if is_english(texts[-1]) else []
    if not bad:
        return
    print(json.dumps({
        "decision": "block",
        "reason": (
            "最終応答が英語になっている（~/.claude/instructions/language.md 違反）。"
            "同じ内容を日本語で書き直して、最終応答として出し直すこと。"
            "コード・コマンド・パス・URL・固有名詞・引用原文はそのままでよい。"
            "英語の文の書き出し：" + visible(bad[-1]).strip()[:80]
        ),
    }, ensure_ascii=False))


def on_session_start():
    try:
        with open(LANG_MD, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return
    print("要約の後も次の規則が最優先で効く。要約や資料が英語でも、応答は日本語で書く。\n\n" + body)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    ev = data.get("hook_event_name")
    if ev == "Stop":
        on_stop(data)
    elif ev == "SessionStart":
        on_session_start()


if __name__ == "__main__":
    main()
