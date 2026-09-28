#!/usr/bin/env python3
"""Claude Code のセッション JSONL を読める Markdown に書き出す。

API Error でセッションが続けられなくなったとき、その会話に溜まっていた
指示・報告・ツール実行結果を回収するために使う。

  python3 rescue-session.py <session.jsonl> -o out.md [--full] [--user-label 名前]

既定ではツール結果を 4000 文字で切る。--full で全文を出す。
ユーザー発話の見出しは --user-label（既定は「ユーザー」）。
"""
import argparse
import json
import sys


def blocks(content):
    if isinstance(content, str):
        return [("text", content)]
    out = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                out.append(("text", b.get("text", "")))
            elif t == "thinking":
                out.append(("thinking", b.get("thinking", "")))
            elif t == "tool_use":
                out.append(("tool_use", (b.get("name"), b.get("input", {}))))
            elif t == "tool_result":
                out.append(("tool_result", (b.get("is_error"), b.get("content"))))
    return out


def render_result(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, dict) and b.get("type") == "image":
                parts.append("[画像]")
            else:
                parts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--user-label", default="ユーザー", help="ユーザー発話の見出しに出す名前")
    a = ap.parse_args()
    limit = None if a.full else a.limit

    rows = []
    for line in open(a.jsonl, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"type": "PARSE_ERROR", "raw": line[:200]})

    seen_text = set()
    o = []
    meta = next((r for r in rows if r.get("sessionId")), {})
    o.append("# セッション記録 %s" % meta.get("sessionId", ""))
    o.append("")
    o.append("- 元ファイル: `%s`" % a.jsonl)
    o.append("- 作業ディレクトリ: `%s`" % meta.get("cwd", ""))
    o.append("- Claude Code: %s" % meta.get("version", ""))
    o.append("- レコード数: %d" % len(rows))
    o.append("")

    for i, d in enumerate(rows, 1):
        m = d.get("message") or {}
        typ = d.get("type")
        ts = (d.get("timestamp") or "")[:19].replace("T", " ")
        if typ == "user":
            for kind, val in blocks(m.get("content")):
                if kind != "text":
                    if kind == "tool_result":
                        is_err, c = val
                        body = render_result(c)
                        if limit and len(body) > limit:
                            body = body[:limit] + "\n…（%d文字省略）" % (len(body) - limit)
                        o.append("結果%s:" % ("（エラー）" if is_err else ""))
                        o.append("")
                        o.append("```")
                        o.append(body)
                        o.append("```")
                        o.append("")
                    continue
                if val.startswith("<") or val.startswith("Caveat:"):
                    continue
                o.append("## [%d] %s %s" % (i, ts, a.user_label))
                o.append("")
                o.append(val)
                o.append("")
        elif typ == "assistant":
            for kind, val in blocks(m.get("content")):
                if kind == "text":
                    if val in seen_text:
                        continue
                    seen_text.add(val)
                    o.append("### [%d] %s Claude" % (i, ts))
                    o.append("")
                    o.append(val)
                    o.append("")
                elif kind == "tool_use":
                    name, inp = val
                    s = json.dumps(inp, ensure_ascii=False, indent=None)
                    if limit and len(s) > limit:
                        s = s[:limit] + " …"
                    o.append("- 実行 `%s`: %s" % (name, s))
        elif typ == "system" and d.get("content"):
            o.append("- system: %s" % str(d.get("content"))[:300])

    open(a.out, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("書き出しました: %s (%d 行)" % (a.out, len(o)), file=sys.stderr)


if __name__ == "__main__":
    main()
