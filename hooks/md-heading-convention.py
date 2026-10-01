#!/usr/bin/env python3
"""PostToolUse hook: enforce living-doc heading convention on .md files.

Reads the hook payload (JSON on stdin), and if the written/edited file is a
.md file, scans its heading lines (#, ##, ...) for:
  (1) full-width or half-width parentheses (subtitles, confirm-dates, "（案）" etc.)
  (2) hedge words (たたき台 / ドラフト / draft / 暫定)
  (3) zero-based chapter/section/step numbering ("## 0.", "Step 0", "第0章" 等)
      — 章・節・連番は「1」から始める（グローバル文書作成規約）。
If any heading offends, prints the offending headings to stderr and exits 2 so
Claude receives the feedback and self-corrects. Otherwise exits 0 silently.

Convention 正本: ~/.claude/conventions/living-doc-structure.md
"""
import sys
import json
import re


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}
    fp = tool_input.get("file_path") or tool_response.get("filePath") or ""
    if not fp.endswith(".md"):
        return 0

    # 履歴・追記型の文書は規約対象外（既存の括弧書き見出しが過去ログとして妥当なため）。
    # Windows は file_path が `\` 区切りで来るため、`/llm-wiki/` 等のパターンが
    # 一致せず除外が無言で効かなくなる。区切りを `/` に正規化してから判定する。
    low = fp.lower().replace("\\", "/")
    if any(s in low for s in (
        "/llm-wiki/", "/log.md", "-work-log.md", "/work-logs/", "changelog", "memory.md"
    )):
        return 0

    content = ""
    try:
        with open(fp, encoding="utf-8") as fh:
            content = fh.read()
    except Exception:
        content = tool_input.get("content", "") or ""
    if not content:
        return 0

    heading_re = re.compile(r"^\s{0,3}#{1,6}\s")
    paren_re = re.compile(r"[（(].*?[）)]")
    hedge_re = re.compile(r"(たたき台|ドラフト|[Dd]raft|暫定)")
    # 章番号・連番は「1」始まり。0 始まりの章/節/ステップ番号を禁止。
    zero_num_re = re.compile(
        r"^(?:"
        r"0(?=[\s.．。、,，)）:：]|$)"                       # 先頭が章番号 0："0" "0." "0 " "0)" "0:"
        r"|0\.\d"                                            # "0.1" 等の小数章番号
        r"|第\s*0\s*[章節部編講話回]"                        # 第0章 / 第0節 …
        r"|0\s*[章節部編講話回](?![0-9])"                    # 0章 / 0節 …
        r"|(?:step|phase|part|chapter|section|appendix|stage|"
        r"day|week|round|level|unit|lesson|module)\s*[.\-:：]?\s*0(?=[\s.．。、,，)）:：]|$)"
        r")",
        re.IGNORECASE,
    )

    offenders = []
    zero_offenders = []
    in_code_block = False
    for line in content.splitlines():
        # Toggle code fence tracking (``` or ~~~)
        if re.match(r"^(`{3,}|~{3,})", line.strip()):
            in_code_block = not in_code_block
        if in_code_block:
            continue
        if heading_re.match(line):
            heading = line.strip()
            text = re.sub(r"^#{1,6}\s+", "", heading)
            if paren_re.search(heading) or hedge_re.search(heading):
                offenders.append(heading)
            if zero_num_re.match(text):
                zero_offenders.append(heading)

    # 日付2行が H1 見出しより前（ファイルの1行目）にあるものを検出する。
    # 規約は「H1 見出しの直後に作成日・改訂日の2行」。本文冒頭を1行目と取り違えやすい。
    date_first = False
    head = [l for l in content.splitlines() if l.strip()][:8]
    if head and re.match(r"^(作成日|改訂日)[：:]", head[0].strip()):
        date_first = any(re.match(r"^#\s", l) for l in head[1:])

    msgs = []
    if date_first:
        msgs.append(
            "【日付規約】作成日・改訂日の2行がファイルの1行目に置かれています。"
            "1行目は H1 見出し（文書の題名）とし、日付の2行はその直後に移してください"
            "（正本: ~/.claude/instructions/principles.md「文書の日付」）。"
        )
    if offenders:
        msgs.append(
            "【見出し規約】次の見出しに括弧書き／ヘッジ語が含まれています。"
            "見出しは名詞句のみとし、副題・確認日・「（案）」「たたき台」「ドラフト」「暫定」等を排除してください。"
            "更新日が必要なら本文冒頭の「作成日：YYYY-MM-DD」「改訂日：YYYY-MM-DD」2行に集約します"
            "（正本: ~/.claude/conventions/living-doc-structure.md）。\n"
            + "\n".join("  - " + o for o in offenders)
        )
    if zero_offenders:
        msgs.append(
            "【章番号規約】次の見出しは章・節・ステップ番号が「0」始まりです。"
            "連番は「1」から始めてください（前置き・導入は「概要」等の名詞見出しにするか本文に置く）"
            "（正本: ~/.claude/conventions/living-doc-structure.md）。\n"
            + "\n".join("  - " + o for o in zero_offenders)
        )

    if msgs:
        # Windows では stderr の既定エンコーディングがコンソールのコードページ
        # （日本語環境では cp932）になり、受け手が UTF-8 として読むため指摘文が
        # 全部化ける。UTF-8 を明示して出す。
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
        print("\n\n".join(msgs), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
