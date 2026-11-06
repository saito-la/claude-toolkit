#!/usr/bin/env python3
"""ディスカッサント・座長の手元メモの md から、iPad で演題ごとに質疑のカードを出す画面（1枚の HTML）を作る。

手元メモの md の形（1演題＝1節。演題でない節も置ける）:

    # 若手医師フォーラム 手元メモ

    ## O2 松尾 紀孝 先生

    15:26 担当（齋藤が先）

    演題の要点など、カードの前に出す本文 …

    ### 称える

    First, I'm glad to see a second-year resident doing research.

    （まず、卒後2年目で研究に取り組んでいることを嬉しく思います。）

    ### 質問

    …

- 節（##）が画面の上のタブ、小節（###）が1枚のカード。小節の前の本文は「概要」のカードになる
- 節の1行目が「時:分」で始まれば、その演題の開始予定の時刻として扱い、続く語（「担当」等）を札にする
- 全体が全角の括弧（…）で囲まれた段落は訳として小さく薄く出す。「訳」ボタンで消せる
- 「- 」で始まる行は箇条書きとして出す

    python3 discussant_cards.py notes.md -o cards.html [--talk 7 --qa 4] [--title "質疑 | 学会名"]

同じ md は markdown-export の md2pdf でそのまま PDF にもなる（正本は md の1つだけ）。
"""
import argparse
import html
import json
import pathlib
import re

TEMPLATE = pathlib.Path(__file__).resolve().parent / "cards-view.html"


def parse(text):
    """md から [{title, time, tag, cards:[{title, text}]}] を返す。"""
    groups = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        parts = re.split(r"^### (.*)$", body, flags=re.M)
        intro, cards = parts[0].strip(), []
        time = tag = ""
        first, _, rest = intro.partition("\n")
        m = re.fullmatch(r"(\d{1,2}:\d{2})\s*(.*)", first.strip())
        if m:
            time, tag, intro = m[1], m[2].strip(), rest.strip()
        if intro:
            cards.append({"title": "概要", "text": intro})
        for t, b in zip(parts[1::2], parts[2::2]):
            cards.append({"title": t.strip(), "text": b.strip()})
        groups.append({"title": head.strip(), "time": time, "tag": tag, "cards": cards})
    if not groups:
        raise SystemExit("「## 題」の節が1つも無い")
    return groups


def h1(text):
    m = re.search(r"^# (.+)$", text, flags=re.M)
    return m[1].strip() if m else "質疑"


def render(groups, talk, qa, title):
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("__TITLE__", html.escape(title))
            .replace("__DATA__", json.dumps(groups, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__TALK__", str(int(talk * 60)))
            .replace("__QA__", str(int(qa * 60))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("notes", type=pathlib.Path, help="手元メモの md")
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True, help="書き出す HTML")
    ap.add_argument("--talk", type=float, default=7, help="1演題の発表の分数（既定 7）")
    ap.add_argument("--qa", type=float, default=4, help="1演題の質疑の分数（既定 4）")
    ap.add_argument("--title", help="ページの題。省略すると md の H1")
    a = ap.parse_args()
    text = a.notes.read_text(encoding="utf-8")
    groups = parse(text)
    a.out.write_text(render(groups, a.talk, a.qa, a.title or h1(text)), encoding="utf-8")
    n = sum(len(g["cards"]) for g in groups)
    print(f"{a.out}（{len(groups)}節・{n}枚・発表 {a.talk:g} 分＋質疑 {a.qa:g} 分）")


if __name__ == "__main__":
    main()
