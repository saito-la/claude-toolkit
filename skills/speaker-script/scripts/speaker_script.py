#!/usr/bin/env python3
"""読み原稿の md から、iPad で読む読み原稿と残り時間のタイマーの画面（1枚の HTML）を作る。

読み原稿の md の形（1スライド＝1節。節の番号がスライドの番号）:

    # 読み原稿 <演題>

    ## 1. 表紙

    0:00

    本文の段落 …

    ## 2. 演題と演者

    0:15

    …

各節の1行目はそのスライドを話し始める目安の時刻（分:秒）。本文は空行で段落に分ける。

    python3 speaker_script.py script.md -o script.html --minutes 20 [--title "読み原稿 | 学会名"]

デッキの組み立てスクリプトから読み込んで、同じ md を発表者ノートにも使える（parse() を呼ぶ）。
"""
import argparse
import html
import json
import pathlib
import re
import sys

TEMPLATE = pathlib.Path(__file__).resolve().parent / "script-view.html"


def parse(text):
    """md の本文から [{n, title, start(秒), start_label, text}] を返す。"""
    parts = re.split(r"^## (\d+)\. (.*)$", text, flags=re.M)
    out = []
    for num, title, body in zip(parts[1::3], parts[2::3], parts[3::3]):
        start, _, rest = body.strip().partition("\n")
        start = start.strip()
        m = re.fullmatch(r"(\d+):(\d{2})", start)
        if not m:
            sys.exit(f"{num}. {title.strip()}：節の1行目が開始の目安の時刻（例 4:30）になっていない：{start!r}")
        out.append({"n": int(num), "title": title.strip(), "start": int(m[1]) * 60 + int(m[2]),
                    "start_label": start, "text": rest.strip()})
    if not out:
        sys.exit("「## 1. 題」の形の節が1つも無い")
    return out


def h1(text):
    m = re.search(r"^# (.+)$", text, flags=re.M)
    return m[1].strip() if m else "読み原稿"


def render(sections, minutes, title):
    data = [{k: s[k] for k in ("n", "title", "start", "text")} for s in sections]
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("__TITLE__", html.escape(title))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__TOTAL__", str(int(minutes * 60))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("script", type=pathlib.Path, help="読み原稿の md")
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True, help="書き出す HTML")
    ap.add_argument("--minutes", type=float, required=True, help="発表の持ち時間（分）。質疑は含めない")
    ap.add_argument("--title", help="ページの題。省略すると md の H1")
    a = ap.parse_args()
    text = a.script.read_text(encoding="utf-8")
    secs = parse(text)
    a.out.write_text(render(secs, a.minutes, a.title or h1(text)), encoding="utf-8")
    chars = sum(len(re.sub(r"\s", "", s["text"])) for s in secs)
    print(f"{a.out}（{len(secs)}枚・本文 {chars} 字・1分300字で約 {chars / 300:.1f} 分・持ち時間 {a.minutes:g} 分）")


if __name__ == "__main__":
    main()
