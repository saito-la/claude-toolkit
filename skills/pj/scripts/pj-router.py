#!/usr/bin/env python3
"""pj-router.py — 自然文の「やりたいこと」から起動すべきプロジェクトを決定する2層ルーター。

  L1  決定的語彙一致（aliases/path/domain/purpose/boundary）— exact-alias/path の単一ヒットのみ即決
  L2  外れたら圧縮カタログを返し、判断は呼び出し側に委ねる（/pj なら Claude 本体、シェルなら fzf）
      このとき候補の並びは pj-ngram.py の文字N-gram類似度で決める（2026-08-29 追加）。
      L1 は「登録済みの語がクエリ文にそのまま含まれるか」しか見ないため日本語の長文は
      ほぼ必ず空振りし、L2 は全98件を並べるだけだった。並べ替えを入れると
      「治験の安全性報告の様式を直したい」で utokyo/CRPC/safety が1位に来る。
      判定そのものは変えていない（DIRECT の条件は従来どおり L1 のみ）。

2026-07-26 に4層 → 2層へ簡素化した。撤去したもの：
  - L0 学習キャッシュ（choices.jsonl）と learn/correct — 再出現クエリ17件はすべて
    `ai` `misc` `cdisc` `infra` 等のプロジェクト名・パス断片で、L1 が既に解決できる
    範囲だった。判定が割れた記録は0件で訂正機構は一度も必要になっていない
  - 旧 L2 のローカル埋込スタブ — no-op のまま実装されなかった
  - 旧 L3 の `claude -p --model haiku` — LLM が LLM を呼ぶ層。呼び出し側の Claude は
    会話文脈を持つぶん haiku より的確に判断できる（/pj の主眼そのもの）

出力（stdout、1行目で分岐）:
  DIRECT<TAB><relpath>                 単一確信ヒット
  CANDIDATES                           2行目以降に <relpath>\t<reason> が続く
  CATALOG                              2行目以降に全プロジェクトの圧縮カタログが続く

Usage:
  pj-router.py route "<query>"
  pj-router.py request "<query>" <relpath> [--body-file <f>] [--file]
      開いた先へ最初の指示として渡す文（query か body に振り分けの1行を付けたもの）を出す。
      query が行き先の名前だけなら何も出さない。--file なら一時ファイルに書いてパスを出す
      （pj-open.sh・pj-orca.sh・pj() が使う。用件を開いた先で打ち直さないため。2026-11-04）
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import re
import sys
import unicodedata

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pj_recommend = _load("pj_recommend", "pj-recommend.py")
pj_ngram = _load("pj_ngram", "pj-ngram.py")
pj_learn = _load("pj_learn", "pj-learn.py")

TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[一-鿿぀-ヿ]{2,}")


def normalize(s):
    return unicodedata.normalize("NFKC", s or "").strip().casefold()


def tokenize(s):
    return set(TOKEN_RE.findall(s))


def ascii_word_subset(a_norm, b_norm):
    """両者がASCIIのみで構成される場合、トークン集合が双方向で部分集合関係にあるかを返す。
    どちらかがCJKを含む場合はNone（呼び出し側は従来の部分文字列一致にフォールバック）。

    tier2のpath/leaf一致は元々「短いクエリがパスに含まれる／長い文にパス全体が含まれる」を
    双方向の生文字列部分一致（`in`）で判定していたが、これだと短いASCIIのleaf名
    （例 "pr", "ta"）が無関係な語の内部に偶然内包されて誤ヒットする
    （実例2026-07-29: query "mealprep" が leaf "pr" を内包し utokyo/CRPC/pr にDIRECT誤起動。
    同様に "meta" は leaf "ta"(cdisc/ta) を内包し得る）。
    tier1のexact-alias判定（ASCII別名はトークン集合の完全一致）と同じ考え方を、
    tier2にも双方向で適用する。
    """
    if not (a_norm.isascii() and b_norm.isascii()):
        return None
    a_tok, b_tok = tokenize(a_norm), tokenize(b_norm)
    if not a_tok or not b_tok:
        return False
    return a_tok <= b_tok or b_tok <= a_tok


def l1_match(q_norm, entries):
    """各エントリの最良tierを判定：
      1 = 完全一致（パス／リーフ名が query と一致）または exact-alias（`aliases` の1件が
          queryに一致。最強信号）。CJKを含む別名はqueryの部分文字列一致、ASCIIのみの
          別名はトークン集合の完全一致（語境界一致）——短いASCII別名（例 "ENT"）が
          無関係な語（例 "department"）に部分文字列として偶然内包され誤ヒットするのを
          防ぐ（実例2026-07-29）。CJK別名はトークン境界の概念が乏しいため従来どおり
          部分文字列一致のままでよい。
      2 = path literal（パス/リーフ名とqueryが部分文字列関係）。ASCIIのみの組はtier1と同様
          トークン集合の部分集合関係（双方向）で判定し、CJKを含む組は部分文字列一致のまま
          ——短いASCII leaf名（例 "pr"）が無関係な語（例 "mealprep"）に内包され誤ヒットする
          のを防ぐ（実例2026-07-29、`ascii_word_subset()`）。
      3 = 弱一致（`domain`の部分文字列、aliasのトークン重複、purpose/boundaryのトークン重複）
    DIRECTになり得るのは1・2のみ（単一ヒット時）。3は常にCANDIDATES止まり。
    `domain`は tier1 に混ぜない：短い一般語（例 "hr"）が長文クエリに偶然部分文字列として
    出現し、無関係プロジェクトを誤って単一確信ヒットさせるのを防ぐ（実例2026-07-11参照）。
    tier3のaliasトークン重複（下記）はTOKEN_REで区切られた語単位の集合演算であり、
    部分文字列一致ではないため同種の問題は生じない（2026-07-29 確認）。

    完全一致を tier1 に置くのは、親と子が同名接頭辞を持つとき（`cdisc` と `cdisc/AMED`
    等7件）に親だけを即決させるため。旧 L0 学習キャッシュが3回の反復で担っていた挙動を、
    履歴に依らず決定的に再現する（2026-07-26 の2層化に伴う補強）。
    """
    q_tokens = tokenize(q_norm)
    result = {}
    for e in entries:
        tier, hits = None, []
        path_norm = normalize(e["path"])
        leaf_norm = normalize(e["path"].rsplit("/", 1)[-1])
        if q_norm and (q_norm == path_norm or q_norm == leaf_norm):
            tier = 1
            hits.append(e["path"])
        if tier is None:
            for a in (e.get("aliases") or []):
                a_norm = normalize(a)
                if not a_norm:
                    continue
                if a_norm.isascii():
                    # ASCII別名は語境界一致のみ（トークン集合の完全一致）。
                    # 部分文字列一致だと短い別名（例 "ENT"）が無関係な語
                    # （例 "department"）に偶然内包されて誤ヒットする
                    # （実例2026-07-29）。
                    a_tokens = tokenize(a_norm)
                    if a_tokens and a_tokens <= q_tokens:
                        tier = 1
                        hits.append(a)
                elif a_norm in q_norm:
                    tier = 1
                    hits.append(a)
        if tier is None:
            # 双方向: 短いクエリがパスに含まれる（従来の grep -qiF 相当）／長い文にパス全体が含まれる
            # ASCIIのみの組は語境界一致（トークン集合の部分集合関係）、CJKを含む組は
            # 従来どおり部分文字列一致（トークン境界の概念が乏しいため）。
            path_match = ascii_word_subset(path_norm, q_norm)
            if path_match is None:
                path_match = bool(path_norm) and (path_norm in q_norm or q_norm in path_norm)
            leaf_match = ascii_word_subset(leaf_norm, q_norm)
            if leaf_match is None:
                leaf_match = bool(leaf_norm) and (leaf_norm in q_norm or q_norm in leaf_norm)
            if path_match or leaf_match:
                tier = 2
                hits.append(e["path"])
        if tier is None:
            for d in (e.get("domain") or []):
                d_norm = normalize(d)
                if d_norm and d_norm in q_norm:
                    tier = 3
                    hits.append(d)
            for a in (e.get("aliases") or []):
                if tokenize(normalize(a)) & q_tokens:
                    tier = 3
                    hits.append(a)
            text_tokens = tokenize(normalize(f"{e.get('purpose', '')} {e.get('boundary', '')}"))
            overlap = text_tokens & q_tokens
            if overlap:
                tier = 3
                hits.extend(sorted(overlap))
        if tier is not None:
            result[e["path"]] = (tier, hits)
    return result


def rank_candidates(paths, matches, scores=None):
    """候補を並べる。tier（決定的一致の強さ）が第一キーで、同 tier 内を N-gram 類似度で
    並べ替える。tier をまたいで類似度で入れ替えないのは、語彙一致のほうが確度が高く、
    類似度は「似ている文字列がある」以上のことを主張しないため。
    """
    labels = {1: "alias一致", 2: "path一致", 3: "部分一致"}
    scores = scores or {}

    def sort_key(p):
        tier, hits = matches[p]
        return (tier, -scores.get(p, 0.0), -len(hits), p)

    out = []
    for p in sorted(paths, key=sort_key):
        tier, hits = matches[p]
        reason = f"{labels[tier]}: {'、'.join(hits[:3])}"
        sc = scores.get(p)
        if sc:
            reason += f"／類似 {sc:.2f}"
        out.append((p, reason))
    return out


def route(query):
    q_norm = normalize(query)
    entries = pj_recommend.load_or_build()

    matches = l1_match(q_norm, entries)

    # 類似度は DIRECT では使わないので、必要になるまで計算しない（DIRECT が最頻の経路）。
    cache = {}

    def scores():
        if "v" not in cache:
            # 過去に選ばれたときのクエリ文を照合テキストへ混ぜる（pj-learn.py）。
            # 学習が読めなくても順位が付かないだけなので、失敗は握りつぶして進む。
            try:
                learned = pj_learn.load()
            except OSError:
                learned = {}
            cache["v"] = pj_ngram.score_entries(query, entries, learned)
        return cache["v"]

    for tier in (1, 2):
        tier_paths = [p for p, (t, _) in matches.items() if t == tier]
        if len(tier_paths) == 1:
            return ("DIRECT", tier_paths[0])
        if len(tier_paths) > 1:
            return ("CANDIDATES", rank_candidates(tier_paths, matches, scores()))
    weak_paths = [p for p, (t, _) in matches.items() if t == 3]
    if weak_paths:
        return ("CANDIDATES", rank_candidates(weak_paths, matches, scores()))

    # L1 が空。カタログを丸ごと返し、会話文脈を持つ呼び出し側に判断させる。
    # 件数は絞らず並べ替えだけを行う：上位が外れていても呼び出し側（fzf の絞り込み、
    # /pj の Claude）が下位から拾い直せる状態を保つため。
    sc = scores()
    ordered = sorted(entries, key=lambda e: (-sc.get(e["path"], 0.0), e["path"]))
    return ("CATALOG", pj_recommend.catalog_lines(ordered))


def cmd_route(query):
    kind, payload = route(query)
    if kind == "DIRECT":
        print(f"DIRECT\t{payload}")
    elif kind == "CANDIDATES":
        print("CANDIDATES")
        for path, reason in payload:
            print(f"{path}\t{reason}")
    else:
        print("CATALOG")
        for line in payload:
            print(line)
    return 0


# 依頼の中身があるかを測るときに、名前を除いた残りから落とす語。「recruitment の作業」
# 「CRPC を開いて」は行き先を言っているだけで、開いた先へ渡す依頼の中身が無い。
FILLER_RE = re.compile(r"[\s\W_]+|作業|プロジェクト|開いて|開く|始めたい|始める|やりたい|したい|やる|する|[のでをにはがとへ]")
MARK = "（振り分け：「{query}」から {relpath} を選んで開いた。行き先が違うと言われたら /pj の「開いた先での訂正」に従う）"


def request_text(query, relpath, body=None):
    """開いた先へ最初の指示として渡す文。末尾に振り分けの1行を付ける。

    body（/pj が作業を移すときの起動文など）があればそれを、無ければ query を本文にする。
    body が無く、query が行き先の名前（パスの各段・別名）だけなら空を返す。
    「presentation」と打って開いた先で、claude が「presentation」を依頼と取り違えないため。
    """
    q = normalize(query)
    if body is None:
        names = [normalize(x) for x in relpath.split("/")]
        for e in pj_recommend.load_or_build():
            if e["path"] == relpath:
                names += [normalize(a) for a in (e.get("aliases") or [])]
        rest = q
        for n in sorted(filter(None, names), key=len, reverse=True):
            rest = rest.replace(n, " ")
        if len(FILLER_RE.sub("", rest)) < 4:
            return ""
        body = query.strip()
    return body.rstrip() + "\n\n" + MARK.format(query=" ".join(query.split()), relpath=relpath)


def cmd_request(argv):
    """request "<原文>" <relpath> [--body-file <f>] [--file]
    --file なら一時ファイルに書いてそのパスを出す（body-file は消す）。渡す文が無ければ
    body-file のパスをそのまま（無ければ空を）出す。"""
    args, body_file, to_file = [], None, False
    it = iter(argv)
    for a in it:
        if a == "--body-file":
            body_file = next(it, None)
        elif a == "--file":
            to_file = True
        else:
            args.append(a)
    if len(args) != 2:
        sys.stderr.write(cmd_request.__doc__ + "\n")
        return 64
    query, relpath = args
    body = pathlib.Path(body_file).read_text(encoding="utf-8") if body_file else None
    text = request_text(query, relpath.strip("/"), body)
    if not to_file:
        if text:
            print(text)
        return 0
    if not text:
        print(body_file or "")
        return 0
    fd, path = tempfile.mkstemp(prefix="pj-request.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    if body_file:
        os.unlink(body_file)
    print(path)
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "request":
        return cmd_request(argv[2:])
    if len(argv) < 2 or argv[1] != "route":
        sys.stderr.write(__doc__)
        return 64
    query = " ".join(argv[2:]).strip()
    if not query:
        sys.stderr.write("route には query が必要です\n")
        return 64
    return cmd_route(query)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
