#!/usr/bin/env python3
"""pj-ngram.py — 日本語クエリを文字 N-gram で照合し、候補を類似度順に並べる。

pj-router.py の L1（決定的語彙一致）が空振りしたとき、および弱一致しか無いときの
並べ替えに使う。L1 は「登録済みの語がクエリ文にそのまま含まれるか」しか見ないため、
日本語の長文はほぼ必ず空振りする。原因は router のトークナイザが日本語を単語に
切れないことで、`[一-鿿぀-ヿ]{2,}` は助詞も含めて連続部分を丸ごと1トークンにする。
「治験の安全性報告の様式を直したい」は全体で1トークンになり、purpose 側のトークンと
交わらない（2026-08-29 実測。全98件が並ぶだけの CATALOG に落ちていた）。

そこで単語に切らず2文字ずつ重ねて刻む。「安全性報告」と「安全性管理」は `安全`・`全性`
を共有するので、語形が違っても部分的に一致する。形態素解析器を入れないのは、辞書という
別の維持対象を増やさないため。pj は日常の入口なので、依存の少なさと起動が止まらない
ことを精度より優先する。

珍しい並びほど重く数える（IDF）。そうしないと「した」「して」のようにどの目的文にも
出る並びで全件が横並びになる。スコアはクエリ側の重み付き被覆率で、doc の長さでは
割らない。目的文を丁寧に書いたプロジェクトが不利になるのを避けるため。

Usage（単体確認）:
  pj-ngram.py "治験の安全性報告の様式を直したい"
"""
import importlib.util
import math
import pathlib
import re
import sys
import unicodedata

ASCII_WORD_RE = re.compile(r"[0-9a-z]+")

# 上位何件を「有望」とみなすか（呼び出し側の表示・件数判断の目安）
TOP_N = 5


def normalize(s):
    return unicodedata.normalize("NFKC", s or "").strip().casefold()


def grams(s):
    """英数字は語単位、日本語は2文字ずつ重ねて刻んだ集合を返す。

    英数字まで2-gram にすると "safety" が "sa","af",... に散って弁別力が落ちる。
    ASCII は空白と語境界で自然に切れるので、そのまま語として扱う方が強い。
    """
    s = normalize(s)
    out = set(ASCII_WORD_RE.findall(s))
    # 英数字部分を空白へ潰してから日本語だけを刻む（英数と日本語をまたぐ 2-gram を作らない）
    cjk = ASCII_WORD_RE.sub(" ", s)
    for i in range(len(cjk) - 1):
        g = cjk[i:i + 2]
        if " " not in g and not g.isspace():
            out.add(g)
    return out


def entry_text(e, extra_queries=None):
    """1エントリの照合対象テキスト。path はスラッシュを空白にして語を分ける。

    extra_queries は「過去にこのプロジェクトが選ばれたときのクエリ文」（学習ストア）。
    渡されれば同じ土俵で照合されるので、二度目の類似案件が当たるようになる。
    """
    parts = [
        e["path"].replace("/", " "),
        " ".join(e.get("aliases") or []),
        " ".join(e.get("domain") or []),
        e.get("purpose") or "",
        e.get("boundary") or "",
    ]
    if extra_queries:
        parts.extend(extra_queries)
    return " ".join(parts)


def build_docs(entries, extra=None):
    extra = extra or {}
    return [(e["path"], grams(entry_text(e, extra.get(e["path"])))) for e in entries]


def build_idf(docs):
    n = len(docs) or 1
    df = {}
    for _, g in docs:
        for x in g:
            df[x] = df.get(x, 0) + 1
    # 全件に出る並び（df == n）は log(1) = 0 になり、自動的に無視される
    return {k: math.log(n / v) for k, v in df.items()}, math.log(n)


def score_entries(query, entries, extra=None):
    """path -> スコア（0.0〜1.0）を返す。クエリ側の重み付き被覆率。

    クエリにしか無い並びは df=0 なので、未知語として最大の重み log(n) を与える。
    これは分母（クエリ全体の重み）にだけ効き、一致しなかったぶんスコアを下げる。
    """
    docs = build_docs(entries, extra)
    idf, unseen_w = build_idf(docs)
    q = grams(query)
    if not q:
        return {p: 0.0 for p, _ in docs}
    total = sum(idf.get(x, unseen_w) for x in q) or 1.0
    return {p: sum(idf.get(x, unseen_w) for x in (q & g)) / total for p, g in docs}


def rank(query, entries, extra=None, limit=None):
    """[(path, score), ...] をスコア降順で返す。同点は path 順で安定させる。"""
    scores = score_entries(query, entries, extra)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:limit] if limit else ordered


def _load_recommend():
    spec = importlib.util.spec_from_file_location(
        "pj_recommend", pathlib.Path(__file__).resolve().parent / "pj-recommend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 64
    query = " ".join(argv[1:]).strip()
    entries = _load_recommend().load_or_build()
    for path, sc in rank(query, entries, limit=10):
        print(f"{sc:.3f}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
