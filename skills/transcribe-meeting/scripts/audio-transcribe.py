#!/usr/bin/env python3
"""音声を Gemini で文字起こしする。

長尺音声はモデルが途中で反復ループに陥り後半を丸ごと欠落させることがあるため、
一定長を超える音声は最初から分割し、チャンクごとに品質を検査して破綻を検知したら
再試行・モデル格上げ・再分割で回復する。

使い方:
  audio-transcribe.py <音声ファイル>              # 長さで自動判定（長尺は分割直行）
  audio-transcribe.py <音声ファイル> --split      # 最初から分割モード
  audio-transcribe.py <_parts/ ディレクトリ>      # 分割済みチャンクをそのまま処理
  audio-transcribe.py <音声> --context ctx.txt    # 固有名詞・発言者候補を文脈として注入
  audio-transcribe.py <音声> --no-derive          # 文字起こしのみ（verbatim/summary を作らない）
  audio-transcribe.py --derive-only <transcript>  # 既存トランスクリプトから verbatim/summary を再生成
  audio-transcribe.py <音声> --model gemini-2.5-pro  # 精度優先でproを使う
"""

import argparse, datetime as _dt, hashlib, json, os, re, shutil, subprocess, sys, time
from collections import Counter
from pathlib import Path

from google import genai
from google.genai import errors, types


# ── モデル・閾値 ────────────────────────────────────────────────
PRO_MODEL        = "gemini-pro-latest"     # 最高精度。無料枠が無く、--model で明示指定したときだけ使う
ESCALATE_MODEL   = "gemini-3.5-flash"      # 品質不良時の格上げ先。lite より強く、無料枠がある
FAST_MODEL       = "gemini-3.5-flash"      # このAPIキーの枠で指定モデルが使えない場合の格下げ先
TRANSCRIBE_MODEL = "gemini-3.5-flash-lite" # 文字起こし既定
POST_MODEL       = "gemini-3.5-flash"      # verbatim / summary 生成（後処理。整形のみのため高速モデルで足りる）
#
# 既定を lite にした根拠（2026-08-04 実測。61分の会議音声・15分×5チャンクで全モデル取り直し）:
#   gemini-3.5-flash-lite … 全チャンク尺の100%まで到達。1チャンクだけ話者ラベルが落ちたが再試行1回で回復。
#                           61分を3分15秒・15円（課金キー）。反復ループ・無音からの捏造なし。
#   gemini-3.1-flash-lite … 5チャンク中2チャンクで改行・話者ラベルを落とし、再試行2回＋再分割でも回復せず
#                           （短く割っても同じ書式で返す）。要確認3区間・16リクエスト・22円。不採用。
#   gemini-2.5-flash-lite … 404「no longer available to new users」。当環境では選択肢にならない。
#   gemini-pro-latest    … 無料枠が無いため方針により不採用（齋藤指示 2026-08-04）。精度も無条件に上ではなく、
#                           既存の pro キャッシュには15分チャンク2本で約6分ずつの末尾欠落があった。
# 注: gemini-2.5-* は新規プロジェクト（新規ユーザー）では generateContent 不可（404）。現行世代を既定にする。
LONG_AUDIO_THRESHOLD_SEC = 15 * 60      # これを超える音声は分割モードへ直行
DEFAULT_CHUNK_MIN = 15                  # 分割時のチャンク長（分）。大きいほど総リクエスト数が減る
MIN_CHARS_PER_SEC = 1.5                 # チャンク文字数の下限目安（下回れば途切れの疑い）
MAX_CHARS_PER_SEC = 20                  # チャンク文字数の上限目安（上回れば尺に対して過大＝内容捏造の疑い。日本語の早口でも実測7-8字/秒程度）
POST_BLOCK_CHARS = 24000                # verbatim/summary を分割処理する塊サイズ

# 公式単価（USD / 100 万トークン）。出典 https://ai.google.dev/gemini-api/docs/pricing（2026-08-04 確認）。
#
# 音声入力（in_audio）を分けている理由: 文字起こしでは入力のほぼ全量が音声で、音声単価は
# テキストの 2〜3.3 倍。テキスト単価だけで計算すると実勢を大きく下回る。旧実装は世代前の
# テキスト単価を放置しており実勢の約 1/3 を表示していて、これが「ほとんど使っていない」という
# コスト誤認の直接原因になった（2026-08-04 調査。docs/automation/20260804-gemini-transcription-cost-investigation.md）。
#
# over_200k は、プロンプトが 200k トークンを超えたときに適用される単価。既定チャンク 15 分は
# 約 22 万トークンで常にこの帯に入るため、無視すると pro のコストを半分に見誤る。
# free_tier は無料枠の有無。False のモデルは 1 リクエスト目から課金される。
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

# 無料枠の1日リクエスト数（RPD）の目安。Google が随時改定するため正本ではない。
# 公式ドキュメントは 2026-08 時点で per-model の数値を掲載しておらず、
# 「AI Studio の Rate limit ダッシュボードで確認せよ」としている（https://aistudio.google.com/rate-limit）。
# ここの値は目安表示専用で、課金・枠の判定に使ってはいけない。
# 環境変数 GEMINI_FLASH_RPD / GEMINI_PRO_RPD、または --rpd で上書き可。
_RPD_FLASH = int(os.environ.get("GEMINI_FLASH_RPD", "250"))
_RPD_PRO = int(os.environ.get("GEMINI_PRO_RPD", "100"))
FREE_TIER_RPD = {
    "gemini-3.6-flash":      _RPD_FLASH,
    "gemini-3.5-flash":      _RPD_FLASH,
    "gemini-3.5-flash-lite": _RPD_FLASH,
    "gemini-3.1-flash-lite": _RPD_FLASH,
    "gemini-2.5-flash":      _RPD_FLASH,
    "gemini-2.5-flash-lite": _RPD_FLASH,
    "gemini-pro-latest":     _RPD_PRO,
    "gemini-2.5-pro":        _RPD_PRO,
}
# 当ツール経由のリクエスト数・トークン数を太平洋時間の日付ごとに積算する（無料枠消費の目安）。
# ⚠ このファイルは Mac ごとのローカル記録で、複数の Mac の消費は合算されない。
# 無料枠の RPD は API キー（Cloud プロジェクト）単位で、どの Mac から投げても同じ枠を食う。
# 2026-07-29 に片方の Mac の集計だけを見て「課金はほぼ未消費」と判定し、不要課金と誤結論した。
# 合算する仕組みは無いので、この集計だけで課金・枯渇を判定してはいけない（表示にも明記する）。
DAILY_TALLY_PATH = Path.home() / ".config" / "claude-toolkit" / "gemini-usage.json"

# 使用APIキーの説明（由来＋指紋）。main() で解決して表示用に保持する。
# キー形式（AQ. / AIza）で無料枠・課金を判定してはいけない：課金が効くかどうかは
# キー形式ではなく、キーが紐づく Cloud プロジェクトの課金状態で決まる（形式は無関係）。
# 形式で「無料枠」と表示していた実装は、課金キーを無料枠と誤表示していた（2026-08-04 修正）。
_API_KEY_DESC = "不明"

# 各 Gemini 呼び出しのトークン消費（stage 別）を記録する
USAGE_LOG: list = []
# 各文字起こし区間の品質判定（一発合格／再試行／格上げ／要確認）を記録する
QUALITY_LOG: list = []
# プログラム開始時刻（経過時間の計測用）
_START = time.time()


def _fmt_dur(sec: float) -> str:
    """秒を「M分S秒」形式に整形する。"""
    sec = int(round(sec or 0))
    m, s = divmod(sec, 60)
    return f"{m}分{s}秒" if m else f"{s}秒"

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac"}
AUDIO_MIME_TYPES = {
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
}


# ── Gemini 呼び出し（リトライ＋トークン記録） ──────────────────────
def _modality_tokens(details, want: str) -> int:
    """prompt_tokens_details から指定モダリティ（AUDIO / TEXT 等）のトークン数を取り出す。"""
    total = 0
    for d in details or []:
        mod = getattr(d, "modality", None)
        name = getattr(mod, "name", None) or str(mod or "")
        if name.upper().endswith(want):
            total += getattr(d, "token_count", 0) or 0
    return total


def _record_usage(stage: str, model: str, resp, sec: float = 0.0) -> None:
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return
    prompt = getattr(um, "prompt_token_count", 0) or 0
    audio = _modality_tokens(getattr(um, "prompt_tokens_details", None), "AUDIO")
    # thinking トークンは candidates に含まれないことがあるが出力として課金される
    thoughts = getattr(um, "thoughts_token_count", 0) or 0
    USAGE_LOG.append({
        "stage": stage, "model": model, "sec": round(sec, 2),
        "prompt": prompt,
        "prompt_audio": audio,                 # 音声単価で課金される入力トークン
        "prompt_text": max(0, prompt - audio),
        "candidates": getattr(um, "candidates_token_count", 0) or 0,
        "thoughts": thoughts,
        "total": getattr(um, "total_token_count", 0) or 0,
    })


def _record_quality(name: str, duration_sec, text: str, ok: bool, reason: str,
                    attempts: int, escalated: bool = False, flagged: bool = False) -> None:
    """1区間の文字起こし品質を記録する（一発合格／再試行回数／格上げ／要確認）。"""
    chars = len(re.sub(r"\s", "", text or ""))
    QUALITY_LOG.append({
        "chunk": name,
        "duration_sec": round(duration_sec, 1) if duration_sec else None,
        "chars": chars,
        "chars_per_sec": round(chars / duration_sec, 2) if duration_sec else None,
        "attempts": attempts, "escalated": escalated,
        "ok": ok, "flagged": flagged, "reason": reason,
    })


def _pt_date() -> str:
    """太平洋時間の日付 YYYY-MM-DD（Google 無料枠のリセット基準＝PT 0時）。"""
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:                        # tzdata 無し等：夏時間(PDT=UTC-7)近似
        return (now_utc - _dt.timedelta(hours=7)).strftime("%Y-%m-%d")


def _bump_daily_tally(model: str, tokens: int) -> None:
    """当ツールの1リクエストを PT 日付・モデル別に積算する（他アプリの消費は含まない）。
    `_machine` にホスト名を記録するのは、後からファイルを見たときに
    「これはどの Mac の記録か・全体ではない」が分かるようにするため。"""
    try:
        DAILY_TALLY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if DAILY_TALLY_PATH.exists():
            data = json.loads(DAILY_TALLY_PATH.read_text(encoding="utf-8"))
        data["_machine"] = os.uname().nodename
        m = data.setdefault(_pt_date(), {}).setdefault(model, {"requests": 0, "tokens": 0})
        m["requests"] += 1
        m["tokens"] += int(tokens or 0)
        days = sorted(k for k in data if not k.startswith("_"))
        for old in days[:-14]:               # 直近14日ぶんだけ保持
            data.pop(old, None)
        DAILY_TALLY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def write_daily_tally_report() -> None:
    """本日（PT基準）当ツールが消費した無料枠の目安を表示する。"""
    try:
        data = json.loads(DAILY_TALLY_PATH.read_text(encoding="utf-8")) if DAILY_TALLY_PATH.exists() else {}
    except (OSError, ValueError):
        return
    day = _pt_date()
    today = data.get(day, {})
    if not today:
        return
    host = data.get("_machine", os.uname().nodename)
    print(f"\n── 本日の消費（この Mac〔{host}〕のみ・太平洋時間 {day} 基準） ──")
    for model, m in sorted(today.items()):
        rpd = FREE_TIER_RPD.get(model)
        req = m["requests"]
        if rpd:
            print(f"  {model:<22}本日 {req} リクエスト / RPD目安 {rpd}（残り約 {max(0, rpd - req)}）"
                  f"  累計トークン {m['tokens']:,}")
        else:
            print(f"  {model:<22}本日 {req} リクエスト  累計トークン {m['tokens']:,}")
    print("  ※ この集計は当ツール経由・この Mac だけのもの。無料枠（RPD）は API キー単位で、")
    print("     他の Mac からの消費も同じ枠を食うが合算されない。RPD目安も Google が随時改定する。")
    print("     → 枠・残高の判定にこの数字を使わないこと。実測は gemini-key-status.py、")
    print("       残枠は https://aistudio.google.com/rate-limit で確認する。")


class QuotaExhaustedError(RuntimeError):
    """再実行しても回復しない 429（無料枠の日次上限 / 前払いクレジット枯渇）。上位で明確に案内して停止する。"""


# 429 の原因別メッセージ。両者は対処が正反対（待てば回復 vs 購入が必要）なので必ず区別して出す。
# 「どちらか分からないまま人が推測する」ことが、無料枠の429を課金枯渇と誤認して
# 不要課金を招いた原因だった（2026-07-29）。API は理由を返しているので推測は不要。
_MSG_DAY = (
    "Gemini API 無料枠の1日リクエスト上限（RPD）に達しました。"
    "当日中は再実行しても回復しません（リセットは太平洋時間0時＝日本時間16時頃）。"
    "→ 枠リセット後に再実行するか、課金を有効にしたキーに切り替えてください。"
    "※ これは課金クレジットの枯渇ではありません。クレジットを購入しても解決しません。"
)
_MSG_CREDITS = (
    "Gemini API の前払いクレジット（Prepay）が枯渇しています。"
    "購入するまで回復しません（待っても戻りません）。"
    "→ https://aistudio.google.com/billing で残高を確認・購入してください。"
    "※ これは無料枠の日次上限ではありません。時間をおいても回復しません。"
)


def _balance_check_hint() -> str:
    """残高確認コマンドが環境変数で登録されていれば案内文に足す（個人環境固有のパスをここに書かないため）。"""
    cmd = os.environ.get("GEMINI_BALANCE_CMD", "").strip()
    return f"\n  残高確認: {cmd}" if cmd else ""


def _is_tier_block(e) -> bool:
    """このモデルがAPIキーの枠で使えない（limit: 0）か。真なら呼び出し側で別モデルへ格下げすべき。"""
    if getattr(e, "code", None) != 429:
        return False
    return "limit: 0" in str(getattr(e, "message", "") or e)


def _quota_kind(e) -> str:
    """429 のクォータ種別を返す。
    'credits'（前払いクレジット枯渇＝購入まで回復しない）／'day'（無料枠の日次上限＝翌日回復）／
    'minute'（分次＝待てば回復）／''（不明）。
    credits を最初に判定する: クレジット枯渇時にリトライしても無駄で、待っても回復しないため。"""
    if getattr(e, "code", None) != 429:
        return ""
    msg = str(getattr(e, "message", "") or e)
    low = msg.lower()
    if "prepayment" in low or "credits are depleted" in low or "credit balance" in low:
        return "credits"
    if "PerDay" in msg or "requests_per_day" in msg:
        return "day"
    if "PerMinute" in msg or "requests_per_minute" in msg:
        return "minute"
    if "free_tier" in msg or "FreeTier" in msg:   # 期間表記が無い free tier は保守的に日次扱い
        return "day"
    return ""


def _raise_if_hard_quota(e):
    """429 のうち再実行で回復しない種別（クレジット枯渇・無料枠日次上限）を
    QuotaExhaustedError に変換して投げる。それ以外なら何もしない。
    generate_content だけでなくアップロード経路からも呼ぶ（生のトレースバックを出さないため）。"""
    kind = _quota_kind(e)
    if kind == "credits":
        raise QuotaExhaustedError(_MSG_CREDITS + _balance_check_hint()) from e
    if kind == "day":
        raise QuotaExhaustedError(_MSG_DAY + _balance_check_hint()) from e


def _retry_delay_seconds(e):
    """エラーメッセージ中の retryDelay 秒数を取り出す（無ければ None）。"""
    msg = str(getattr(e, "message", "") or e)
    m = re.search(r"retry(?:Delay)?['\"\s:in]+?(\d+)\s*s", msg, re.I)
    return int(m.group(1)) if m else None


def generate_with_retry(client, stage: str, max_attempts: int = 4, **kwargs):
    """generate_content を実行。分次レート(429)・サーバー過負荷(503/500)は限定的にリトライし、
    日次上限(429 free tier / PerDay)は当日回復しないため即 QuotaExhaustedError で停止する
    （＝失敗を長引かせず、無料枠を無駄に消費しない）。"""
    for attempt in range(1, max_attempts + 1):
        try:
            t0 = time.time()
            resp = client.models.generate_content(**kwargs)
            _record_usage(stage, kwargs.get("model", ""), resp, time.time() - t0)
            um = getattr(resp, "usage_metadata", None)
            _bump_daily_tally(kwargs.get("model", ""),
                              getattr(um, "total_token_count", 0) if um else 0)
            return resp
        except (errors.ServerError, errors.ClientError) as e:
            code = getattr(e, "code", None)
            if _is_tier_block(e):
                raise                        # モデル未提供。呼び出し側で格下げ
            _raise_if_hard_quota(e)
            transient = code in (429, 500, 503)
            if not transient or attempt == max_attempts:
                raise
            if code == 429:                  # 分次レート：サーバー指定の待機を尊重
                wait = min(120, (_retry_delay_seconds(e) or 30) + 2)
            elif code == 503:                # 過負荷：短く抑える（長引く時は早めに諦める）
                wait = min(30, 8 * attempt)
            else:
                wait = min(60, 5 * 2 ** (attempt - 1))
            print(f"\n  一時的なエラー（{code}）。{wait}秒後にリトライ ({attempt}/{max_attempts})",
                  end="", flush=True)
            time.sleep(wait)


def _quality_summary() -> dict:
    """QUALITY_LOG を集計して品質サマリ dict を返す（区間なしなら空 dict）。"""
    if not QUALITY_LOG:
        return {}
    n = len(QUALITY_LOG)
    first_try = sum(1 for q in QUALITY_LOG
                    if q["ok"] and q["attempts"] == 1 and not q["escalated"])
    recovered = sum(1 for q in QUALITY_LOG
                    if q["ok"] and (q["attempts"] > 1 or q["escalated"]))
    flagged = [q for q in QUALITY_LOG if q["flagged"]]
    cps = [q["chars_per_sec"] for q in QUALITY_LOG if q["chars_per_sec"]]
    covered = sum(q["duration_sec"] or 0 for q in QUALITY_LOG)
    chars = sum(q["chars"] for q in QUALITY_LOG)
    if flagged:
        grade = "C（要確認区間あり）"
    elif recovered:
        grade = "B（再試行・格上げで回復）"
    else:
        grade = "A（全区間が一発合格）"
    return {
        "segments": n, "pass_first_try": first_try, "recovered": recovered,
        "flagged": len(flagged),
        "covered_min": round(covered / 60, 1) if covered else None,
        "total_chars": chars,
        "chars_per_sec_avg": round(sum(cps) / len(cps), 2) if cps else None,
        "chars_per_sec_min": min(cps) if cps else None,
        "grade": grade,
        "flagged_chunks": [q["chunk"] for q in flagged],
    }


def write_quality_report():
    """文字起こし品質のサマリを表示する（区間ごとの一発合格／回復／要確認）。"""
    q = _quality_summary()
    if not q:
        return
    print("\n── 文字起こし品質 ──")
    print(f"  総合評価: {q['grade']}")
    print(f"  区間数 {q['segments']} ／ 一発合格 {q['pass_first_try']} ／ "
          f"再試行・格上げで回復 {q['recovered']} ／ 要確認 {q['flagged']}")
    if q["chars_per_sec_avg"] is not None:
        print(f"  発話密度 平均 {q['chars_per_sec_avg']} 字/秒"
              f"（最小 {q['chars_per_sec_min']} 字/秒。低いほど途切れの疑い）")
    if q["covered_min"] is not None:
        print(f"  処理カバレッジ 約 {q['covered_min']} 分 ／ 総文字数 {q['total_chars']:,} 字")
    if q["flagged_chunks"]:
        print(f"  ⚠ 要確認区間: {', '.join(q['flagged_chunks'])}")


def _call_cost(u: dict) -> float:
    """1リクエストの概算課金額（USD）。音声／テキストを別単価で、200k 超は上位帯の単価で計算する。
    階層はリクエスト単位で決まるため、モデル別に合算してから掛けると誤る（合算すると全リクエストが
    200k 超に見える）。ここで1件ずつ計算し、呼び出し側で足し上げる。"""
    pr = PRICING.get(u["model"])
    if not pr:
        return 0.0
    if u["prompt"] > 200_000 and pr.get("over_200k"):
        pr = {**pr, **pr["over_200k"]}
    out_tokens = u.get("candidates", 0) + u.get("thoughts", 0)
    return (u.get("prompt_audio", 0) / 1e6 * pr["in_audio"]
            + u.get("prompt_text", u["prompt"]) / 1e6 * pr["in"]
            + out_tokens / 1e6 * pr["out"])


def write_usage_report(out_dir: Path, stem: str):
    """stage 別・モデル別のトークン消費・所要時間・品質を表示し、_usage.json に保存する。"""
    if not USAGE_LOG and not QUALITY_LOG:
        return None
    by_key: dict = {}
    for u in USAGE_LOG:
        d = by_key.setdefault((u["stage"], u["model"]),
                              {"calls": 0, "prompt": 0, "prompt_audio": 0, "candidates": 0,
                               "thoughts": 0, "total": 0, "sec": 0.0, "usd": 0.0})
        d["calls"] += 1
        for f in ("prompt", "prompt_audio", "candidates", "thoughts", "total"):
            d[f] += u.get(f, 0)
        d["sec"] += u.get("sec", 0.0)
        d["usd"] += _call_cost(u)
    total_tokens = sum(u["total"] for u in USAGE_LOG)
    api_sec = sum(u.get("sec", 0.0) for u in USAGE_LOG)
    elapsed = time.time() - _START
    rows, est_cost = [], 0.0
    for (stage, model), d in sorted(by_key.items()):
        est_cost += d["usd"]
        rows.append({"stage": stage, "model": model, **d,
                     "sec": round(d["sec"], 1), "est_usd": round(d.pop("usd"), 4)})
    billable = [m for m in sorted({r["model"] for r in rows})
                if not PRICING.get(m, {}).get("free_tier", True)]
    quality = _quality_summary()
    report = {"total_tokens": total_tokens, "est_usd_approx": round(est_cost, 4),
              "est_jpy_approx": round(est_cost * 155),
              "api_key": _API_KEY_DESC,   # どのキーで消費したかを後から追えるようにする（値は含まない）
              "no_free_tier_models": billable,
              "elapsed_sec": round(elapsed, 1), "elapsed_human": _fmt_dur(elapsed),
              "api_sec": round(api_sec, 1),
              "note": "est_usd は公式単価（音声入力単価・200k超の階層を反映）による概算。"
                      "無料枠の消化分は差し引いていないため、無料枠内なら実請求は 0 になる。"
                      "elapsed_sec は本コマンドの総経過時間、api_sec は Gemini 応答待ちの合計。",
              "quality": quality, "by_stage": rows,
              "quality_detail": QUALITY_LOG, "calls": USAGE_LOG}
    path = out_dir / f"{stem}_usage.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    models_used = sorted({r["model"] for r in rows})
    print(f"\n── 使用モデル ──\n  {', '.join(models_used)}　／　APIキー: {_API_KEY_DESC}")
    print("\n── トークン消費（Gemini API） ──")
    for r in rows:
        print(f"  {r['stage']:<14}{r['model']:<22}calls={r['calls']:>2}  "
              f"total={r['total']:>9,}  (in={r['prompt']:,}〔音声 {r['prompt_audio']:,}〕"
              f" / out={r['candidates'] + r['thoughts']:,})  "
              f"{r['sec']:>6.1f}秒  ~${r['est_usd']}")
    print(f"  合計 {total_tokens:,} トークン ／ 概算 ${round(est_cost, 4)}（約 {round(est_cost * 155):,} 円）")
    print("  ※ 公式単価による概算（音声入力単価・200k超の階層を反映）。無料枠で賄えた分は実請求されない。")
    if billable:
        print(f"  ⚠ 無料枠が無いモデルを使用: {', '.join(billable)}（1リクエスト目から課金）")
    print(f"\n── 所要時間 ──")
    print(f"  総経過 {_fmt_dur(elapsed)}（うち Gemini 応答待ち {_fmt_dur(api_sec)}）")
    write_quality_report()
    write_daily_tally_report()
    print(f"\n  レポート保存: {path.name}")
    return path


# ── 品質チェック ────────────────────────────────────────────────
def _detect_loop(text: str) -> str:
    """文/段落レベルの反復（長尺文字起こしの典型的破綻）を検出する。"""
    segs = [s.strip() for s in re.split(r'[。\n]', text) if len(s.strip()) >= 20]
    if len(segs) < 3:
        return ""
    seg, n = Counter(segs).most_common(1)[0]
    if n >= 3:
        return f"文/段落の反復を検出（{n}回）: {seg[:30]!r}…"
    uniq = len(set(segs)) / len(segs)
    if len(segs) >= 12 and uniq < 0.5:
        return f"ユニーク文比率が低い（{uniq:.0%}）＝反復の疑い"
    return ""


def _detect_short_cycle_loop(text: str) -> str:
    """短い発言の往復（ピンポン型）反復ループを検出する。
    「話者A: X／話者B: Y」のような1〜3行の周期が5回以上連続すると失格にする。
    _detect_loop は20字未満の短い発言を対象外にするため、この種の破綻（例：
    「鈴木先生。」/「鈴木先生も、そう。」の連続）を単独では検知できない。"""
    lines = [re.sub(r'^\[?\d{1,2}:\d{2}\]?\s*', '', l).strip()
             for l in text.splitlines() if l.strip()]
    n = len(lines)
    i = 0
    while i < n:
        for period in (1, 2, 3):
            if i + period * 5 > n:
                continue
            cycle = lines[i:i + period]
            reps = 1
            j = i + period
            while j + period <= n and lines[j:j + period] == cycle:
                reps += 1
                j += period
            if reps >= 5:
                return f"短周期の反復ループを検出（{period}行周期×{reps}回）: {cycle}"
        i += 1
    return ""


TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
# 末尾欠落と判定する被覆率。チャンク尺のこの割合まで最終タイムスタンプが届かなければ失格。
# 0.75 なのは、末尾の無音が長い区間での誤検出を避けつつ（15分チャンクなら3分45秒の無音まで許容）、
# 実際に起きた「後半6分が丸ごと消える」規模の欠落は確実に捕まえるため。
MIN_TS_COVERAGE = 0.75


def _detect_truncation(text: str, duration_sec: float) -> str:
    """タイムスタンプが尺の末尾まで届いているかで、後半の欠落を検出する。
    文字数だけの下限判定（MIN_CHARS_PER_SEC=1.5）では緩すぎて末尾欠落を通してしまう：
    2026-08-04 に pro のキャッシュを検証したところ、15分チャンクの 9:07 / 8:48 で
    出力が終わっている（各約6分の欠落）のに「合格」扱いでキャッシュされていた。"""
    if not duration_sec or duration_sec < 300:
        return ""
    ts = [int(a) * 60 + int(b) for a, b in TS_RE.findall(text)]
    if not ts:
        return "タイムスタンプが1つも無く、どこまで文字起こしされたか検証できない"
    last = max(ts)
    if last < duration_sec * MIN_TS_COVERAGE:
        return (f"最終タイムスタンプ {last//60}:{last%60:02d} が尺 "
                f"{int(duration_sec)//60}:{int(duration_sec)%60:02d} に対して早すぎる"
                f"（被覆 {last/duration_sec:.0%}）＝後半の欠落の疑い")
    return ""


# 書式崩れの理由に付ける印。再分割しても直らない種類の失敗であることを示す
# （2026-08-04 実測：1行化した区間を 15分→7分→4分 と割っても同じモデルは同じように1行で返した）。
FORMAT_PREFIX = "書式崩れ: "
# 書式チェックを適用する最小の尺（秒）。ffmpeg の分割は末尾に 0 秒前後の端切れを作ることがあり、
# そこに「行数が少ない」を適用すると無限に再試行して無駄なリクエストを消費する。
FORMAT_CHECK_MIN_SEC = 60


def _detect_format_break(text: str, duration_sec: float = None) -> str:
    """話者ラベル・改行というプロンプトの指定が守られているかを検査する。
    守られないと後段の話者比定（apply-speaker-mapping.py）が成立しない。
    lite 系は 5チャンク中2チャンク程度でこれを落とす（2026-08-04 実測）。"""
    if duration_sec is not None and duration_sec < FORMAT_CHECK_MIN_SEC:
        return ""                              # 短い端切れ区間は数行で正常
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if len(lines) < 5:
        return FORMAT_PREFIX + f"改行されておらず1塊で出力されている（{len(lines)}行）＝話者分離が失われている"
    labels = len(re.findall(r"話者[A-Z]", text))
    if labels < 3:
        return FORMAT_PREFIX + f"話者ラベルがほとんど付与されていない（{labels}個 / {len(lines)}行）"
    return ""


def _shift_timestamps(text: str, offset_sec: float) -> str:
    """[MM:SS] を offset_sec だけ後ろにずらす（再分割したサブ区間を親の時間軸に戻す）。"""
    if not offset_sec:
        return text
    off = int(offset_sec)

    def _fix(m):
        t = int(m.group(1)) * 60 + int(m.group(2)) + off
        return f"[{t // 60}:{t % 60:02d}]"

    return TS_RE.sub(_fix, text)


def normalize_lines(text: str) -> str:
    """1塊で返された出力を、タイムスタンプの直前で改行して行に戻す。
    モデルが改行指定を無視しても、[MM:SS] が入っていれば発言の区切りは復元できる。
    再試行より確実で無料（2026-08-04 実測：1行化は再試行・再分割では直らなかった）。
    ただし話者ラベルまでは復元できないため、書式チェック自体は残す。"""
    t = (text or "").strip()
    lines = [l for l in t.splitlines() if l.strip()]
    if len(lines) > 2 or len(TS_RE.findall(t)) < 5:
        return text
    return TS_RE.sub(lambda m: "\n" + m.group(0), t).strip()


def check_quality(text: str, min_chars: int = None, max_chars: int = None,
                  duration_sec: float = None) -> tuple:
    """文字化け・ループ・途切れ・捏造・末尾欠落・書式崩れを検出。(ok, reason) を返す。"""
    t = (text or "").strip()
    if not t:
        return False, "空の出力"
    m = re.search(r'(.)\1{9,}', t)                       # 同一文字の連続
    if m:
        return False, f"同一文字の連続を検出: {m.group(0)[:20]!r}"
    reason = _detect_loop(t)                             # 長い文/段落の反復ループ
    if reason:
        return False, reason
    reason = _detect_short_cycle_loop(t)                 # 短い発言の反復ループ（ピンポン型）
    if reason:
        return False, reason
    if min_chars and len(re.sub(r'\s', '', t)) < min_chars:   # 尺に対して短すぎ＝途切れ
        return False, f"文字数が想定を大きく下回る（{len(t)}字 / 目安{min_chars}字）＝途切れの疑い"
    if max_chars and len(re.sub(r'\s', '', t)) > max_chars:   # 尺に対して多すぎ＝内容捏造の疑い
        return False, f"文字数が尺に対して過大（{len(t)}字 / 上限目安{max_chars}字）＝無音・短尺区間からの内容捏造（ハルシネーション）の疑い"
    reason = _detect_truncation(t, duration_sec)              # 末尾欠落（タイムスタンプ被覆）
    if reason:
        return False, reason
    reason = _detect_format_break(t, duration_sec)            # 話者ラベル・改行の崩れ
    if reason:
        return False, reason
    lines = [l.strip() for l in t.splitlines() if l.strip()]  # 短行が過半（既存）
    if len(lines) > 20:
        short = sum(1 for l in lines if len(l) <= 3)
        if short / len(lines) > 0.5:
            return False, f"短行が {short}/{len(lines)} 行 を占める"
    return True, ""


def collapse_loops(text: str) -> str:
    """隣接する完全重複行を1回に畳む（末尾ループの最終防衛）。"""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and out and s == out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out)


# ── プロンプト ──────────────────────────────────────────────────
def build_transcribe_prompt(context_hint: str = "") -> str:
    p = """この音声は会議の録音です。日本語で忠実に文字起こししてください。
- 話者が変わるたびに改行し、行頭に「話者A:」「話者B:」…と付す（この音声ファイル内で一貫した記号を使う）
- 各発言の冒頭に [MM:SS] のタイムスタンプを付す
- 聞き取れない箇所は [不明] と記す
- 相槌・言い淀みは残してよい。内容は省略しない
- 音声の最後まで文字起こしする。同じ文や段落を繰り返してはならない
- 出力は文字起こしテキストのみ（前置き・説明は不要）"""
    if context_hint.strip():
        p += "\n\n## 参考：固有名詞・専門用語の優先表記および発言者候補\n" + context_hint.strip()
    return p


VERBATIM_PROMPT = """以下の会議文字起こしから、フィラー（えー、あのー、そのー、えっと、あの、まあ（文頭の意味のない使用）、なんか（意味のない使用）など）と明らかな言い淀み（同じ語の直後の繰り返し）のみを除去してください。

ルール：
- フィラーと言い淀み以外の内容は一切省略しない
- 話者ラベル（「話者A:」等、実名や確度記号〔◎〕〔○〕〔△〕〔？〕付きに置換済みの場合はその表記）と発言順序・タイムスタンプを保持する
- 各発言は「話者ラベル: 発言内容」の1行形式を維持する
- 同じ文や段落を繰り返さない
- 出力は処理後のテキストのみ（説明文不要）

---
{text}"""

CONDENSED_PROMPT = """以下の会議文字起こしを凝縮してください。

ルール：
- 発言の本質（事実・意見・意思決定・背景）を完全に保ちつつ、重複や冗長な表現を排除する
- 具体的な数値・固有名詞・技術的課題・提案内容は省略厳禁
- 口語特有の繰り返し・言い直し・無意味な相槌・冗長な説明を削ぎ落とし、事務的で洗練された文章に再構成する
- 断片的な発言を文脈ごとに一貫性のある段落として統合する（発言者が混在してよい）
- 話題の切り替わりごとに ## レベルの小見出しを付ける
- 話者名は段落冒頭または文中で自然に示す。確度記号は要約では簡略表記にする：**〔◎〕（確実）は付けない**（例「古賀部長より：…」）、〔○〕は「○」、〔△〕は「△」、〔？〕は「？」を氏名直後に付す（例「金子○より：…」「話者不明？」）
- 意味のない相槌のみの行は削除する
- 同じ内容を繰り返さない
- 出力はMarkdownのみ（説明文不要）

---
{text}"""

MERGE_PROMPT = """以下は同一会議を時系列の区間ごとに凝縮した要約の連結です。全体を1本の議事録要約に統合してください。

ルール：
- 区間をまたぐ重複を排除し、話題ごとに ## 小見出しで再構成する
- 事実・数値・固有名詞・意思決定・話者名を落とさない。確度記号は簡略表記（〔◎〕は付けない、〔○〕→「○」、〔△〕→「△」、〔？〕→「？」）
- 時系列と論理の流れを保つ
- 出力はMarkdownのみ（説明文不要）

---
{text}"""


# ── 音声処理（ffmpeg / ffprobe） ─────────────────────────────────
def probe_duration(path: Path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
        return float(out.strip())
    except Exception:
        return None


def split_audio(audio: Path, seg_seconds: int, tag: str = "parts") -> list:
    """ffmpeg で seg_seconds 秒ごとに分割し、チャンクのパス一覧を返す。"""
    parts_dir = audio.parent / f"{audio.stem}_{tag}"
    parts_dir.mkdir(exist_ok=True)
    print(f"音声を {seg_seconds/60:.0f} 分チャンクに分割中: {audio.name} → {parts_dir.name}/")
    subprocess.run([
        "ffmpeg", "-i", str(audio),
        "-f", "segment", "-segment_time", str(int(seg_seconds)),
        "-c", "copy", "-reset_timestamps", "1", "-loglevel", "error",
        str(parts_dir / f"{audio.stem}_{tag}%02d{audio.suffix}"),
    ], check=True)
    chunks = sorted(parts_dir.glob(f"{audio.stem}_{tag}*{audio.suffix}"))
    for c in chunks:
        print(f"  {c.name}  ({c.stat().st_size / 1e6:.1f} MB)")
    return chunks


def _upload(client, path: Path):
    """ファイルをアップロードし ACTIVE になるまで待って file オブジェクトを返す。"""
    # 非ASCIIファイル名だと google-genai が HTTP ヘッダーのエンコードに失敗するため、
    # パス文字列ではなくファイルオブジェクトを渡す。
    mime = AUDIO_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    # アップロードも 429 を返す（クレジット枯渇はここで先に出る）。generate_content 側と同じ
    # 分類・リトライを通さないと生のトレースバックになり原因が読めない（2026-08-04 実際に発生）。
    up = None
    for attempt in range(1, 4):
        try:
            with open(path, "rb") as fh:
                up = client.files.upload(
                    file=fh, config=types.UploadFileConfig(mime_type=mime, display_name=path.name))
            break
        except (errors.ClientError, errors.ServerError) as e:
            _raise_if_hard_quota(e)
            code = getattr(e, "code", None)
            if code not in (429, 500, 503) or attempt == 3:
                raise
            wait = min(60, (_retry_delay_seconds(e) or 15) + 2)
            print(f"\n  アップロード一時エラー（{code}）。{wait}秒後に再試行 ({attempt}/3)",
                  end="", flush=True)
            time.sleep(wait)
    for _ in range(80):
        f = client.files.get(name=up.name)
        if f.state.name == "ACTIVE":
            return f
        if f.state.name == "FAILED":
            client.files.delete(name=up.name)
            raise RuntimeError(f"アップロード失敗: {path.name}")
        print(".", end="", flush=True)
        time.sleep(3)
    client.files.delete(name=up.name)
    raise RuntimeError(f"タイムアウト: {path.name} が ACTIVE になりませんでした")


# 課金枠で pro が使えない等の理由で一度格下げしたら、以降のチャンクも同じモデルを使う
_FORCED_MODEL = None


def transcribe_file(client, path: Path, prompt: str, model: str, stage: str = "transcribe") -> str:
    global _FORCED_MODEL
    use = _FORCED_MODEL or model
    print(f"  [{use}] {path.name} アップロード中", end="", flush=True)
    f = _upload(client, path)
    print(" → 文字起こし中...", end="", flush=True)
    try:
        resp = generate_with_retry(
            client, stage, model=use, contents=[prompt, f],
            config=types.GenerateContentConfig(temperature=0.0))
    except errors.ClientError as e:
        client.files.delete(name=f.name)
        if _is_tier_block(e) and use != FAST_MODEL:
            print(f"\n  ⚠ {use} はこのAPIキーの課金枠で利用不可。{FAST_MODEL} に切替えて継続します。")
            _FORCED_MODEL = FAST_MODEL
            return transcribe_file(client, path, prompt, FAST_MODEL, stage)
        raise
    except Exception:                        # QuotaExhaustedError 等でもアップロード済みファイルを掃除
        try:
            client.files.delete(name=f.name)
        except Exception:
            pass
        raise
    client.files.delete(name=f.name)
    print(" 完了")
    return resp.text or ""


# 品質不良時に pro へ自動格上げするか。--no-escalate で無効化する。
# 無効化が必要な理由: コストや課金枠の都合で「このモデルだけで走らせる」と決めた実行を、
# 自動格上げが黙って破ってしまう（2026-08-04 に実際に発生。flash 指定で走らせたのに
# 1チャンクの品質不良から pro に格上げされ、pro を使わない前提が崩れた）。
_ESCALATE_ENABLED = True


def escalate(model: str) -> str:
    """品質不良時の格上げ先を返す。格上げ無効なら、または既に格上げ先以上ならそのまま。
    格上げ先を pro ではなく ESCALATE_MODEL にしているのは、無料枠の無いモデルへ自動で
    移ると「無料枠のあるモデルだけを使う」方針（齋藤指示 2026-08-04）が黙って破られるため。
    pro を使いたいときは --model gemini-pro-latest で明示する。"""
    if not _ESCALATE_ENABLED:
        return model
    return ESCALATE_MODEL if model not in (ESCALATE_MODEL, PRO_MODEL) else model


def transcribe_with_recovery(client, path: Path, prompt: str, model: str,
                             stage: str = "transcribe", duration_sec: float = None,
                             depth: int = 0) -> str:
    """1チャンクを文字起こしし、品質不良なら再試行→格上げ→再分割で回復する。"""
    min_chars = int(duration_sec * MIN_CHARS_PER_SEC) if duration_sec else None
    max_chars = int(duration_sec * MAX_CHARS_PER_SEC) if duration_sec else None
    attempts = []
    for attempt in range(2):                       # 初回＋再試行1
        m = model if attempt == 0 else escalate(model)
        text = normalize_lines(collapse_loops(transcribe_file(client, path, prompt, m, stage)))
        ok, reason = check_quality(text, min_chars=min_chars, max_chars=max_chars,
                                   duration_sec=duration_sec)
        if ok:
            _record_quality(path.name, duration_sec, text, True, "",
                            attempt + 1, escalated=(m != model))
            return text
        print(f"    ⚠ 品質不良: {reason} → 再試行（{attempt + 1}/2）")
        attempts.append((text, reason))
    # 再分割（5分より長く、深さ上限未満なら半分に割って個別処理。子区間が各自品質を記録）。
    # 書式崩れ（話者ラベル・改行）は再分割しても直らないので試さない：同じモデルは短くしても
    # 同じ書式で返す。無駄なリクエストを増やすだけになる（2026-08-04 実測）。
    format_only = all(r.startswith(FORMAT_PREFIX) for _, r in attempts)
    if duration_sec and duration_sec > 300 and depth < 2 and not format_only:
        print(f"    ↳ {path.name} をさらに分割して再処理")
        subs = split_audio(path, max(150, int(duration_sec / 2)), tag=f"sub{depth}")
        # 各サブ区間のタイムスタンプは 0:00 から振り直されるため、親区間の先頭からの
        # 経過秒でずらしてから連結する。ずらさないと連結結果の最終タイムスタンプが尺に
        # 届かず、次回実行時にキャッシュ検証が必ず失格を出して永久に取り直しになる。
        out, offset = [], 0.0
        for c in subs:
            d = probe_duration(c)
            out.append(_shift_timestamps(
                transcribe_with_recovery(client, c, prompt, escalate(model), stage, d, depth + 1),
                offset))
            offset += d or 0
        return "\n".join(out)
    text, reason = max(attempts, key=lambda a: len(a[0]))
    print(f"    ✗ 品質を確保できず。該当区間に注記を付与: {reason}")
    _record_quality(path.name, duration_sec, text, False, reason,
                    2, escalated=True, flagged=True)
    return f"[要確認: 文字起こし品質低下（{reason}）]\n{text}"


def _chunk_cache_path(chunk: Path) -> Path:
    """チャンクの文字起こし結果を保存するサイドカーパス（<chunk>.txt）。"""
    return chunk.with_name(chunk.name + ".txt")


def transcribe_chunks(client, chunks: list, prompt: str, model: str) -> str:
    """チャンクを順次文字起こしして Part ヘッダー付きで結合する。
    成功済みチャンクは <chunk>.txt にキャッシュし、再実行時は品質を満たすものを再利用する
    （失敗ジョブを丸ごと再実行しても全チャンクを再送信せず、無料枠の無駄消費を防ぐ）。"""
    if not chunks:
        raise FileNotFoundError("音声チャンクが見つかりません")
    n = len(chunks)
    rpd = FREE_TIER_RPD.get(model)
    rpd_note = f"無料枠RPD目安 {rpd}回/日（PT基準・要確認）。" if rpd else ""
    print(f"{n} チャンクを処理します"
          f"（推定リクエスト数 約{n}〜{n * 2}回。{rpd_note}"
          f"済チャンクはキャッシュ再利用）")
    parts, covered = [], 0.0
    for i, chunk in enumerate(chunks):
        d = probe_duration(chunk)
        covered += d or 0
        min_chars = int(d * MIN_CHARS_PER_SEC) if d else None
        max_chars = int(d * MAX_CHARS_PER_SEC) if d else None
        cache = _chunk_cache_path(chunk)
        if cache.exists() and cache.stat().st_size > 0:
            cached = cache.read_text(encoding="utf-8")
            ok, why = check_quality(cached, min_chars=min_chars, max_chars=max_chars,
                                    duration_sec=d)
            if ok:                                    # 品質を満たす済チャンクのみ再利用
                print(f"[{i + 1}/{n}] キャッシュ再利用: {cache.name}")
                parts.append(f"## Part {i + 1} — {chunk.name}\n\n{cached}")
                continue
            print(f"[{i + 1}/{n}] キャッシュを破棄して取り直し（{why}）")
        print(f"[{i + 1}/{n}]", end=" ")
        text = transcribe_with_recovery(client, chunk, prompt, model, "transcribe", d)
        cache.write_text(text, encoding="utf-8")      # チェックポイント保存
        parts.append(f"## Part {i + 1} — {chunk.name}\n\n{text}")
    print(f"カバレッジ: 約 {covered/60:.1f} 分ぶんのチャンクを処理")
    return "\n\n---\n\n".join(parts)


# ── verbatim / summary の生成（長尺は分割処理） ──────────────────
def _split_for_post(text: str, max_chars: int = POST_BLOCK_CHARS) -> list:
    """Part 区切りを優先しつつ max_chars 以下の塊にまとめる。"""
    parts = re.split(r'(?=^## Part )', text, flags=re.M)
    blocks, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > max_chars:
            blocks.append(cur)
            cur = p
        else:
            cur += p
    if cur.strip():
        blocks.append(cur)
    return blocks or [text]


def _summary_marker_style(text: str) -> str:
    """凝縮版（summary）の確度記号を簡略表記にする（transcript/verbatim には適用しない）。
    〔◎〕（確実）は付けない ／ 〔○〕→○ ／ 〔△〕→△ ／ 〔？〕→？。氏名直後に残った空白も整える。"""
    text = re.sub(r"〔◎[^〕]*〕", "", text)          # 確実は記号を落とす
    text = text.replace("〔○〕", "○").replace("〔△〕", "△").replace("〔？〕", "？")
    text = re.sub(r"〔([○△？])[^〕]*〕", r"\1", text)  # 注釈付き（例〔○・推定〕）も記号のみに
    text = re.sub(r"[ 　]+([、。：:）)])", r"\1", text)  # 記号除去で生じた余分な空白
    return text


GENERIC_STEM_RE = re.compile(
    r"^(untitled|new[ _-]?recording(\s*\d+)?|recording(\s*\d+)?|rec\d*|"
    r"voice[ _-]?memo(s)?(\s*\d+)?|img[ _-]?\d+|vid[ _-]?\d+|audio[ _-]?\d+|\d+|"
    r"録音(\s*\d+)?|無題)$",
    re.IGNORECASE,
)


def looks_generic(stem: str) -> bool:
    """録音機器・OSの既定ファイル名（Untitled、New Recording 等）かどうかを判定する。"""
    return bool(GENERIC_STEM_RE.match(stem.strip()))


TITLE_PROMPT = """以下は会議の凝縮要約です。この内容を表すタイトルを2行で出力してください（他は一切出力しない）。

1行目: 日本語タイトル（15字以内、体言止め、括弧・記号・「」なし）
2行目: 同じ内容を表す英語 kebab-case のスラッグ（2〜5語、ハイフン区切り、すべて小文字、日付や拡張子は付けない）

---
{text}"""


def derive_title(client, summary_text: str):
    """要約から (日本語タイトル, kebab-case スラッグ) を生成する。失敗時は (None, None)。"""
    try:
        resp = generate_with_retry(client, "title", model=POST_MODEL,
                                   contents=TITLE_PROMPT.format(text=summary_text[:6000])).text or ""
    except Exception:
        return None, None
    lines = [l.strip(" 　#*「」") for l in resp.strip().splitlines() if l.strip()]
    if not lines:
        return None, None
    if len(lines) < 2:
        return lines[0], None
    title, raw_slug = lines[0], lines[1]
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", raw_slug.lower())).strip("-")
    return title, (slug or None)


def organize_outputs(out_dir: Path, stem: str, audio: Path = None) -> Path:
    """成果物を整理する：`<stem>_summary.md` だけを直下に残し、音声・チャンク・
    中間txt（transcript/verbatim）・usage.json を `<out_dir>/<stem>/` に一括する。破壊しない（移動のみ）。"""
    dest = out_dir / stem
    moved = []

    def _move(src: Path):
        if not src.exists():
            return
        tgt = dest / src.name
        if src.resolve() == tgt.resolve():
            return
        dest.mkdir(exist_ok=True)
        if tgt.exists():                     # 再実行時は古い同名を置き換え
            shutil.rmtree(tgt) if tgt.is_dir() else tgt.unlink()
        shutil.move(str(src), str(tgt))
        moved.append(src.name)

    for suffix in ("_transcript.txt", "_verbatim.txt", "_usage.json"):
        _move(out_dir / f"{stem}{suffix}")
    search = {out_dir}
    if audio:
        search.add(audio.parent)
    for base in search:                      # チャンク作業ディレクトリ（byproduct）
        for pat in (f"{stem}_parts*", f"{stem}_sub*"):
            for d in sorted(base.glob(pat)):
                _move(d)
        for ext in AUDIO_SUFFIXES:           # 直下に置かれた音声
            _move(base / f"{stem}{ext}")
    if audio and audio.exists():             # 入力音声（別ディレクトリでも）を <stem> 名で格納
        dest.mkdir(exist_ok=True)
        tgt = dest / f"{stem}{audio.suffix}"
        if audio.resolve() != tgt.resolve():
            if tgt.exists():
                tgt.unlink()
            shutil.move(str(audio), str(tgt))
            moved.append(audio.name)
    if moved:
        print(f"\n成果物を整理: {stem}_summary.md を直下に残し、{len(moved)}件を {dest.name}/ に一括")
    return dest


def derive_files(client, transcript: str, out_dir: Path, stem: str) -> tuple:
    """トランスクリプトから verbatim（ケバ取り）と summary（凝縮）を生成する。
    stem が録音機器の既定名（Untitled 等）の場合、内容から生成したタイトルでファイル一式をリネームする。
    戻り値は (verbatim_out, summary_out, 最終的な stem)。"""
    transcript_path = out_dir / f"{stem}_transcript.txt"
    verbatim_out = out_dir / f"{stem}_verbatim.txt"
    summary_out = out_dir / f"{stem}_summary.md"
    blocks = _split_for_post(transcript)

    print(f"\nケバ取り版を生成中（{len(blocks)}ブロック）...", end="", flush=True)
    vparts = []
    for b in blocks:
        vt = generate_with_retry(client, "verbatim", model=POST_MODEL,
                                 contents=VERBATIM_PROMPT.format(text=b)).text or ""
        vparts.append(collapse_loops(vt))
    verbatim_out.write_text("\n".join(vparts).strip() + "\n", encoding="utf-8")
    print(f" 完了: {verbatim_out.name}")

    print(f"凝縮版を生成中（{len(blocks)}ブロック）...", end="", flush=True)
    sparts = []
    for b in blocks:
        st = generate_with_retry(client, "summary", model=POST_MODEL,
                                 contents=CONDENSED_PROMPT.format(text=b)).text or ""
        sparts.append(st.strip())
    if len(sparts) == 1:
        summary = sparts[0]
    else:                                          # 区間要約を1本に統合
        summary = generate_with_retry(client, "summary-merge", model=POST_MODEL,
                                      contents=MERGE_PROMPT.format(text="\n\n".join(sparts))).text or ""
    summary = _summary_marker_style(collapse_loops(summary).strip())

    title, slug = derive_title(client, summary)
    body = f"# {title}\n\n{summary}\n" if title else summary + "\n"

    new_stem = stem
    if slug and looks_generic(stem):
        new_stem = slug
        for src, dst in (
            (transcript_path, out_dir / f"{new_stem}_transcript.txt"),
            (verbatim_out, out_dir / f"{new_stem}_verbatim.txt"),
        ):
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        verbatim_out = out_dir / f"{new_stem}_verbatim.txt"
        summary_out = out_dir / f"{new_stem}_summary.md"
        print(f"\nタイトルを内容から生成: {stem} → {new_stem}")

    summary_out.write_text(body, encoding="utf-8")
    print(f" 完了: {summary_out.name}")
    return verbatim_out, summary_out, new_stem


# ── API キー・GUI ───────────────────────────────────────────────
def _load_api_key_from_config() -> str:
    config_file = Path.home() / ".config" / "claude-toolkit" / "gemini-api-key"
    if config_file.exists():
        return config_file.read_text(encoding="utf-8").strip()
    return ""


def _resolve_api_key() -> tuple:
    """(キー, 由来の説明) を返す。環境変数が設定ファイルより優先される。
    由来を明示するのは、キーを複数アカウント分（無料枠／課金）持てる構成で
    「実際にどのキーが使われているか」が分からず 429 の原因を誤認した経緯があるため
    （2026-07-29 に無料枠の日次上限を課金枯渇と誤認して不要課金、2026-07-30 に
    dotfiles と ~/.zshrc の二重管理で古いキーが使われていたことが判明）。"""
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env, "環境変数 GEMINI_API_KEY"
    cfg = _load_api_key_from_config()
    if cfg:
        return cfg, "~/.config/claude-toolkit/gemini-api-key"
    return "", "(未設定)"


def _key_fingerprint(key: str) -> str:
    """キー識別用の指紋（sha256 先頭8桁）。値を晒さずに、どのキーが使われたかを
    後から突き合わせられるようにする（ログ・レポートに残しても安全）。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _run_gui() -> tuple:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.lift()
    audio = filedialog.askopenfilename(
        title="文字起こしする音声ファイルを選択してください",
        filetypes=[("音声ファイル", "*.m4a *.mp3 *.wav *.aac *.ogg *.flac"),
                   ("すべてのファイル", "*.*")])
    if not audio:
        root.destroy()
        sys.exit("ファイルが選択されませんでした。")
    out_dir = filedialog.askdirectory(
        title="保存先フォルダを選択してください（キャンセルで音声ファイルと同じ場所）")
    root.destroy()
    stem = Path(audio).stem
    folder = out_dir if out_dir else str(Path(audio).parent)
    return audio, str(Path(folder) / f"{stem}_transcript.txt")


# ── メイン ──────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="音声を Gemini で文字起こし（長尺は分割・品質検査・回復つき）")
    p.add_argument("audio", nargs="?", help="音声ファイル または _parts/ ディレクトリ")
    p.add_argument("--output", "-o", help="トランスクリプト出力パス（省略時は自動命名）")
    p.add_argument("--api-key", help="APIキーを明示指定（既定: 環境変数 GEMINI_API_KEY → 設定ファイル）")
    p.add_argument("--chunk-minutes", type=int, default=DEFAULT_CHUNK_MIN, metavar="N",
                   help=f"分割時のチャンク長（分）。デフォルト: {DEFAULT_CHUNK_MIN}")
    p.add_argument("--split", action="store_true", help="最初から分割モードで実行")
    p.add_argument("--model", default=TRANSCRIBE_MODEL, help=f"文字起こしモデル。デフォルト: {TRANSCRIBE_MODEL}")
    p.add_argument("--rpd", type=int, metavar="N",
                   help="無料枠の1日リクエスト数（RPD）目安を上書き（本日消費表示用。既定 flash=250）")
    p.add_argument("--context", help="固有名詞・発言者候補を書いたテキストファイル（プロンプトに注入）")
    p.add_argument("--no-derive", action="store_true", help="verbatim/summary を生成しない（文字起こしのみ）")
    p.add_argument("--derive-only", metavar="TRANSCRIPT",
                   help="既存トランスクリプトから verbatim/summary だけ再生成する")
    p.add_argument("--gui", action="store_true", help="ファイル選択ダイアログを表示して実行")
    p.add_argument("--no-escalate", action="store_true",
                   help=f"品質不良時に {ESCALATE_MODEL} へ自動格上げしない（--model で指定したモデルだけを使う）")
    p.add_argument("--no-organize", action="store_true",
                   help="成果物の整理をしない（既定は summary.md 以外を <stem>/ に一括）")
    a = p.parse_args()
    if a.rpd:                                # RPD 目安を上書き（対象モデル＋既定 flash）
        FREE_TIER_RPD[a.model] = a.rpd
        FREE_TIER_RPD["gemini-2.5-flash"] = a.rpd

    if a.api_key:
        key_origin = "コマンドライン --api-key"
    else:
        a.api_key, key_origin = _resolve_api_key()
    if not a.api_key:
        sys.exit("エラー: Gemini API キーが未設定。環境変数 GEMINI_API_KEY を設定するか、"
                 "~/.config/claude-toolkit/gemini-api-key にキーを保存してください。"
                 "（取得: https://aistudio.google.com/apikey）")
    global _API_KEY_DESC, _ESCALATE_ENABLED
    _ESCALATE_ENABLED = not a.no_escalate
    _API_KEY_DESC = f"{key_origin} ／ 指紋 {_key_fingerprint(a.api_key)}"
    # 実行の冒頭に出す。途中でエラー終了しても「どのモデル・どのキーで動いたか」が
    # 必ず残るようにするため（末尾のレポートだけだと失敗時に何も分からない）。
    esc = f"品質不良時は {ESCALATE_MODEL} へ格上げ" if _ESCALATE_ENABLED else "格上げなし（--no-escalate）"
    print(f"── Gemini API ──\n  モデル: {a.model}（文字起こし）／ {POST_MODEL}（後処理）／ {esc}")
    print(f"  キー: {_API_KEY_DESC}")
    print("  ※ キー形式では無料枠／課金は判別できない（紐づく Cloud プロジェクトの課金状態で決まる）")
    client = genai.Client(api_key=a.api_key)

    # ── derive-only モード ──────────────────────────────────
    if a.derive_only:
        tpath = Path(a.derive_only).expanduser().resolve()
        if not tpath.exists():
            sys.exit(f"エラー: トランスクリプトが見つかりません: {tpath}")
        transcript = tpath.read_text(encoding="utf-8")
        stem = tpath.stem[:-len("_transcript")] if tpath.stem.endswith("_transcript") else tpath.stem
        # 文字起こし時の usage.json があれば取り込み、トークン・品質を上書きせず累積表示する
        prev = tpath.parent / f"{stem}_usage.json"
        if prev.exists():
            try:
                data = json.loads(prev.read_text(encoding="utf-8"))
                USAGE_LOG[:0] = data.get("calls", [])
                QUALITY_LOG[:0] = data.get("quality_detail", [])
            except (ValueError, OSError):
                pass
        old_stem = stem
        v, s, stem = derive_files(client, transcript, tpath.parent, stem)
        if stem != old_stem and prev.exists():
            prev.unlink()                    # 旧stemのusage.jsonは新stem側に統合されるため削除
        write_usage_report(tpath.parent, stem)
        if not a.no_organize:
            organize_outputs(tpath.parent, stem)
        print(f"\n生成ファイル:\n  {tpath.parent / (stem + '_summary.md')}"
              f"\n  （他は {tpath.parent / stem}/ に格納）")
        return

    if a.gui or not a.audio:
        a.audio, a.output = _run_gui()

    model = a.model
    context_hint = ""
    if a.context:
        context_hint = Path(a.context).expanduser().read_text(encoding="utf-8")
    prompt = build_transcribe_prompt(context_hint)

    target = Path(a.audio).expanduser().resolve()

    # 出力パスと stem を決定
    if a.output:
        out = Path(a.output).expanduser().resolve()
    elif target.is_dir():
        out = target.parent / f"{target.stem.removesuffix('_parts')}_transcript.txt"
    else:
        out = target.parent / f"{target.stem}_transcript.txt"
    stem = out.stem[:-len("_transcript")] if out.stem.endswith("_transcript") else out.stem

    # ── チャンクディレクトリが渡された場合 ──────────────────
    if target.is_dir():
        chunks = sorted(f for f in target.iterdir()
                        if f.suffix in AUDIO_SUFFIXES and "_part" in f.name)
        result = transcribe_chunks(client, chunks, prompt, model)
    else:
        if not target.exists():
            sys.exit(f"エラー: ファイルが見つかりません: {target}")
        if target.suffix not in AUDIO_SUFFIXES:
            sys.exit(f"エラー: 対応していない形式: {target.suffix}")

        duration = probe_duration(target)
        long_audio = duration and duration > LONG_AUDIO_THRESHOLD_SEC
        if a.split or long_audio:
            why = "指定" if a.split else f"{duration/60:.0f}分 > {LONG_AUDIO_THRESHOLD_SEC//60}分"
            print(f"分割モード（{why}）: {target.name}")
            chunks = split_audio(target, a.chunk_minutes * 60, "parts")
            result = transcribe_chunks(client, chunks, prompt, model)
        else:
            print(f"単一ファイルモード: {target.name}")
            text = collapse_loops(transcribe_file(client, target, prompt, model))
            min_chars = int(duration * MIN_CHARS_PER_SEC) if duration else None
            ok, why = check_quality(text, min_chars=min_chars, duration_sec=duration)
            if ok:
                result = text
                _record_quality(target.name, duration, text, True, "", 1)
                print("品質チェック: OK")
            else:
                print(f"\n⚠ 品質チェック失敗 — {why}\n自動で分割モードに切り替えます...")
                chunks = split_audio(target, a.chunk_minutes * 60, "parts")
                result = transcribe_chunks(client, chunks, prompt, model)

    out.write_text(result, encoding="utf-8")
    print(f"\n文字起こし完了: {out}")

    if not a.no_derive:
        _, _, stem = derive_files(client, result, out.parent, stem)

    write_usage_report(out.parent, stem)
    # 整理（既定）：summary.md 以外（音声・チャンク・transcript/verbatim・usage）を <stem>/ に一括。
    # --no-derive 時は summary が無い＝整理せず transcript を直下に残す（話者比定の作業用）。
    organized = not a.no_derive and not a.no_organize
    if organized:
        organize_outputs(out.parent, stem, audio=target if target.is_file() else None)
    print("\n生成ファイル:")
    if organized:
        print(f"  {out.parent / (stem + '_summary.md')}")
        print(f"  （他は {out.parent / stem}/ に格納）")
    else:
        print(f"  {out.parent / (stem + '_transcript.txt')}")
        if not a.no_derive:
            print(f"  {out.parent / (stem + '_verbatim.txt')}")
            print(f"  {out.parent / (stem + '_summary.md')}")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhaustedError as e:
        print(f"\n⛔ {e}", file=sys.stderr)
        print("  成功済みチャンクはキャッシュ済みです。解決後に同じコマンドを再実行すれば、"
              "未処理のチャンクだけが処理されます（再送信・二重課金は起きません）。", file=sys.stderr)
        sys.exit(2)
