"""Gemini API の公式単価表。**このファイルが単価の正本**。

出典 <https://ai.google.dev/gemini-api/docs/pricing>（2026-08-04 確認）。単位は USD / 100万トークン。

なぜ独立したモジュールなのか:
  単価表は `audio-transcribe.py`（実行時のコスト表示）と `backfill-cost-log.py`
  （過去の `_usage.json` を現行単価で引き直す）の両方が必要とする。かつては両者が
  同じ表を複製し「単価を改定したときは両方直す」というコメントで運用していたが、
  2箇所にある値はいずれズレる。改定を1箇所に閉じるため切り出した（2026-08-08）。

なぜ使わないモデルの単価も載っているのか:
  過去の実行を現行単価で計算し直すには、**もう使わないモデルの単価も必要**だから。
  `gemini-pro-latest` や `gemini-2.5-flash` は 2026-07〜08 の実行に登場する。
  一方 `audio-transcribe.py` は使えるモデルを1つに絞っており（`TRANSCRIBE_MODEL`）、
  この表をそのまま参照はしない——実行経路に持ち込むのは自分が呼べるモデルの行だけにして、
  「使わないモデルの単価が検証されないまま実行時計算に紛れ込む」のを防いでいる。

表の作りについて:
  - `in_audio` を `in` と分けているのは、文字起こしでは入力のほぼ全量が音声で、音声単価が
    テキストの 2〜3.3 倍になるため。テキスト単価だけで計算すると実勢を大きく下回る。
    旧実装は世代前のテキスト単価を放置して実勢の約 1/3 を表示しており、これが
    「ほとんど使っていない」というコスト誤認の直接原因になった（2026-08-04 調査。
    ai-environment `docs/automation/20260804-gemini-transcription-cost-investigation.md`）。
  - `over_200k` は、プロンプトが 200k トークンを超えたときに適用される単価。既定チャンク
    15分は約 22 万トークンなので、15分より長いチャンクを指定したときだけこの帯に入る。
    **階層はリクエスト単位で決まる。** モデル別に合算してから掛けると、全リクエストが
    200k 超に見えて誤る。1件ずつ `rate()` を引くこと。
  - `free_tier` は無料枠の有無。False のモデルは無料枠キーだと 1 リクエスト目から
    `quota limit: 0` で弾かれる（課金キーなら最初から課金される）。
"""

PRICING = {
    "gemini-pro-latest":       {"in": 2.00, "in_audio": 2.00, "out": 12.00, "free_tier": False,
                                "over_200k": {"in": 4.00, "in_audio": 4.00, "out": 18.00}},
    "gemini-3.1-pro-preview":  {"in": 2.00, "in_audio": 2.00, "out": 12.00, "free_tier": False,
                                "over_200k": {"in": 4.00, "in_audio": 4.00, "out": 18.00}},
    "gemini-3.6-flash":        {"in": 1.50, "in_audio": 1.50, "out": 7.50,  "free_tier": True},
    "gemini-3.5-flash":        {"in": 1.50, "in_audio": 1.50, "out": 9.00,  "free_tier": True},
    "gemini-3.5-flash-lite":   {"in": 0.30, "in_audio": 0.30, "out": 2.50,  "free_tier": True},
    "gemini-3.1-flash-lite":   {"in": 0.25, "in_audio": 0.50, "out": 1.50,  "free_tier": True},
    "gemini-2.5-flash":        {"in": 0.30, "in_audio": 1.00, "out": 2.50,  "free_tier": True},
    "gemini-2.5-flash-lite":   {"in": 0.10, "in_audio": 0.30, "out": 0.40,  "free_tier": True},
}

JPY_PER_USD = 155           # 表示用の換算レート。厳密な会計用ではない


def rate(model: str, prompt_tokens: int, table: dict = None) -> dict:
    """1リクエストに適用される単価を返す（200k 超なら上位帯へ差し替える）。
    未知のモデルは空 dict。呼び出し側は 0 円として扱うこと（勝手に既定単価を当てない）。

    `table` を渡せば絞り込んだ表で引ける。`audio-transcribe.py` が自分の使えるモデルだけの
    表を渡し、それ以外のモデルが実行時計算に紛れ込まないようにするために使う。"""
    pr = (table if table is not None else PRICING).get(model)
    if not pr:
        return {}
    if prompt_tokens > 200_000 and pr.get("over_200k"):
        return {**pr, **pr["over_200k"]}
    return pr
