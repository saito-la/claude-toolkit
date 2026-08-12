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
  audio-transcribe.py --check-numbers <transcript> <summary>  # 要約の数値欠落を検査（API不要・無課金）
  audio-transcribe.py --organize-only <transcript>            # 成果物の整理だけ（API不要・無課金）

**このスクリプトが Gemini を呼ぶのは文字起こしだけ。** ケバ取り（verbatim）・凝縮（summary）・
タイトル生成は Gemini に投げず Claude 側で行う（齋藤方針 2026-08-05、2026-08-08 に呼び出しを削除）。
音声を文字にする工程だけが Gemini を必要とし、整形は契約済みの Claude で追加課金なくできる。
実測では後処理が Gemini 課金の大半を占めていた（記録18件・1,464円のうち後処理 585円。
61分の会議1件では総額177円のうち後処理162円）。手順は SKILL.md Step 4。

モデルは gemini-3.5-flash-lite の1つだけを使い、枠が尽きたらモデルではなくキーを替える
（齋藤指示 2026-08-08）。理由は下の「モデルを1つに固定した根拠」を参照。
"""

import argparse, datetime as _dt, hashlib, json, os, re, shutil, subprocess, sys, time
from collections import Counter
from pathlib import Path

from google import genai
from google.genai import errors, types

# 単価表は同じディレクトリの gemini_pricing.py が正本。通常の CLI 実行では sys.path[0] が
# スクリプトのディレクトリになるので import できるが、importlib で読み込まれた場合
# （テスト等）はそうならない。実体ディレクトリを明示的に足して両方で動くようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_pricing import PRICING as _PRICE_TABLE, rate as _rate


# ── モデル・閾値 ────────────────────────────────────────────────
TRANSCRIBE_MODEL = "gemini-3.5-flash-lite" # 文字起こし。**このスクリプトが使う唯一のモデル**
#
# モデルを1つに固定した根拠（2026-08-04 実測。61分の会議音声・15分×5チャンクで全モデル取り直し。
# 正本は ai-environment/docs/automation/20260804-transcription-model-comparison.md）:
#   gemini-3.5-flash-lite … 全チャンク尺の100%まで到達。1チャンクだけ話者ラベルが落ちたが再試行1回で回復。
#                           61分を6リクエスト・約4分・19円。反復ループ・無音からの捏造なし。
#   gemini-3.1-flash-lite … 5チャンク中2チャンクで改行・話者ラベルを落とし、再試行2回＋2段の再分割でも
#                           回復せず（短く割っても同じ書式で返す）。実運用21リクエスト・34円・8分37秒で
#                           要確認2区間が残る。**フォールバックにもしない**（枠を3.5倍食って結果が悪い）。
#   gemini-2.5-flash-lite … 404「no longer available to new users」。当環境では選択肢にならない。
#   gemini-pro-latest    … 無料枠が無い。精度も上ではなく、pro キャッシュには15分チャンク2本で
#                           約6分ずつの末尾欠落があった（被覆 61%・59%）。単価と精度は連動しない。
#
# **枠が尽きたときはモデルではなくキーを替える**（齋藤指示 2026-08-08）。無料枠のクォータは
# 「Cloud プロジェクト × モデル」単位（429 のメトリクスに model 次元が付く。2026-08-08 実測）
# なので他モデルへ逃げる手もあるが、上のとおり品質を満たす代替モデルが無い。キーを増やすほうが
# 品質を落とさずに枠を増やせる（GEMINI_API_KEY_POOL 参照）。
# 注: gemini-2.5-* は新規プロジェクト（新規ユーザー）では generateContent 不可（404）。
LONG_AUDIO_THRESHOLD_SEC = 15 * 60      # これを超える音声は分割モードへ直行
DEFAULT_CHUNK_MIN = 15                  # 分割時のチャンク長（分）。大きいほど総リクエスト数が減る
MIN_CHARS_PER_SEC = 1.5                 # チャンク文字数の下限目安（下回れば途切れの疑い）
MAX_CHARS_PER_SEC = 20                  # チャンク文字数の上限目安（上回れば尺に対して過大＝内容捏造の疑い。日本語の早口でも実測7-8字/秒程度）

# 公式単価（USD / 100 万トークン）。出典 https://ai.google.dev/gemini-api/docs/pricing（2026-08-04 確認）。
#
# 音声入力（in_audio）を分けている理由: 文字起こしでは入力のほぼ全量が音声で、音声単価は
# テキストの 2〜3.3 倍。テキスト単価だけで計算すると実勢を大きく下回る。旧実装は世代前の
# テキスト単価を放置しており実勢の約 1/3 を表示していて、これが「ほとんど使っていない」という
# コスト誤認の直接原因になった（2026-08-04 調査。docs/automation/20260804-gemini-transcription-cost-investigation.md）。
#
# 単価の正本は gemini_pricing.py（全モデルの表。過去の実行を引き直す backfill-cost-log.py も
# 同じ表を読む）。ここでは**本スクリプトが呼べるモデルの行だけ**を取り出して使う。
# 全モデルの表をそのまま実行経路へ持ち込まないのは、使わないモデルの単価が検証されないまま
# 計算に紛れ込むのを防ぐため。--model の choices もこの PRICING から作る。
PRICING = {m: _PRICE_TABLE[m] for m in (TRANSCRIBE_MODEL,)}

# 無料枠の1日リクエスト数（RPD）の目安。Google が随時改定するため正本ではない。
# 公式ドキュメントは 2026-08 時点で per-model の数値を掲載しておらず、
# 「AI Studio の Rate limit ダッシュボードで確認せよ」としている（https://aistudio.google.com/rate-limit）。
# ここの値は目安表示専用で、課金・枠の判定に使ってはいけない。
# 環境変数 GEMINI_FLASH_RPD、または --rpd で上書き可。
# ⚠ この枠は「Cloud プロジェクト × モデル」単位（2026-08-08 実測）。キーを増やせば枠も増える。
_RPD_FLASH = int(os.environ.get("GEMINI_FLASH_RPD", "250"))
FREE_TIER_RPD = {
    "gemini-3.5-flash-lite": _RPD_FLASH,
}
# 当ツール経由のリクエスト数・トークン数を太平洋時間の日付ごとに積算する（無料枠消費の目安）。
# ⚠ このファイルは Mac ごとのローカル記録で、複数の Mac の消費は合算されない。
# 無料枠の RPD は API キー（Cloud プロジェクト）単位で、どの Mac から投げても同じ枠を食う。
# 2026-07-29 に片方の Mac の集計だけを見て「課金はほぼ未消費」と判定し、不要課金と誤結論した。
# 合算する仕組みは無いので、この集計だけで課金・枯渇を判定してはいけない（表示にも明記する）。
DAILY_TALLY_PATH = Path.home() / ".config" / "claude-toolkit" / "gemini-usage.json"

# 実行1回ぶんのコスト明細を1行の JSON として追記していく累積ログ（JSON Lines）。
# 上の gemini-usage.json は「PT日付 × モデル」のリクエスト数・トークン数しか持たず、
# 案件名・stage 別・thinking の内訳・格上げの有無を残さない。そのため
# 「どの案件のどの工程に何円使ったか」を後から辿れず、2026-07〜08 の課金枯渇の原因究明で
# 散在する <stem>_usage.json を find で拾い集める必要が生じた（成果物を移動・削除すると失われる）。
# 追記専用（1実行=1行）にして履歴を消さない。閲覧は gemini-cost-report.py。
#
# **マシンごとに別ファイルへ書く。** 同一ファイルへ複数マシンから追記すると、git で同期した
# ときに必ず末尾行が衝突する。ファイルを分ければ衝突せず、閲覧側でディレクトリを読んで合算できる。
# ディレクトリ自体を同期対象（git リポジトリ等）への symlink にすれば全マシンの消費が1コマンドで
# 見える。本スクリプトは同期の方法を知らない——公開リポジトリの配布物が個人環境のパスを
# 前提にしないため（配線は各自の環境側で行う）。
COST_LOG_DIR = Path.home() / ".config" / "claude-toolkit" / "gemini-cost"
COST_LOG_PATH = COST_LOG_DIR / f"{os.uname().nodename.split('.')[0]}.jsonl"

# 案件名（会議名・人名）はコストログに書かず、この端末ローカルの地図にだけ置く。
# `.local.json` は gitignore 対象にする運用（機密区分上コミットできない会議名が
# 混ざるのを構造的に防ぐ。詳細は _job_id のドキュメント）。
JOB_MAP_PATH = COST_LOG_DIR / "job-names.local.json"

# 使用APIキーの説明（由来＋指紋）。main() で解決して表示用に保持する。
# キー形式（AQ. / AIza）で無料枠・課金を判定してはいけない：課金が効くかどうかは
# キー形式ではなく、キーが紐づく Cloud プロジェクトの課金状態で決まる（形式は無関係）。
# 形式で「無料枠」と表示していた実装は、課金キーを無料枠と誤表示していた（2026-08-04 修正）。
_API_KEY_DESC = "不明"

# 課金状態の実測プローブ（無料枠を持たないモデルへ ping して 429 の種別を読む）は
# 2026-08-08 に廃止した。プールに入れるキーを無料枠キーだけに限定する運用へ変えたため、
# 実行ごとに「課金されるか」を測る必要が無くなった（齋藤指示 2026-08-08）。
# 代わりに、プールへ入れた時点で無料枠であることを利用者が宣言する（GEMINI_API_KEY_POOL）。
# レポートはその宣言を tier_source="declared" として記録し、実測と区別できるようにする。
# ⚠ 宣言なので、キーの Cloud プロジェクトで課金を有効にすると黙って実請求に変わる。
# 疑わしいときは gemini-key-status.py ではなく https://aistudio.google.com/billing で確認する。
_TIER_DECLARED = "unknown"

# ラベル（GEMINI_API_KEY_<ラベル> の <ラベル>）から、そのキーがどのアカウント・どの
# Cloud プロジェクトのものかを引く任意の対応表。**キーの値は書かない**（指紋とラベルだけで
# 足りる）。未設定でも動く：その場合の表示はラベルと指紋に留める。
# 例: {"nho": {"account": "user@example.org", "project": "my-proj", "plan": "free"}}
KEY_ACCOUNTS_PATH = Path.home() / ".config" / "claude-toolkit" / "gemini-accounts.json"

# 指紋 → 課金状態の記録（実測プローブ時代の遺産。2026-08-08 以降は更新されない）。
# 参照するのは「無料枠と宣言したキーが paid として記録されていないか」の照合だけ。
# 食い違いは黙って実請求が始まる唯一の経路なので、実行後のサマリで警告する。
KEY_TIER_PATH = Path.home() / ".config" / "claude-toolkit" / "gemini-key-tier.json"

# 各 Gemini 呼び出しのトークン消費（stage 別）を記録する
USAGE_LOG: list = []
# 各文字起こし区間の品質判定（一発合格／再試行／格上げ／要確認）を記録する
QUALITY_LOG: list = []
# いま処理中のチャンク名と、それを処理しているキーのラベル。_record_usage が消費を
# チャンクへ帰属させるために読む（逐次処理なのでグローバルで足りる）。区間ごとに
# 「どのキーで・何トークン・何秒かかったか」を出せないと、キーがローテーションした
# 実行で「どの区間がどの枠を食ったか」を後から追えない。
_CURRENT_CHUNK: str = ""
_CURRENT_KEY_LABEL: str = ""

# ラベル → 指紋。プールに載せた全キーぶんを main で作る。**値そのものは持たない**
# （指紋は sha256 の先頭8桁で、キーを復元できない。表示・ログに出せるのはこちらだけ）。
_KEY_FINGERPRINTS: dict = {}
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


def _record_usage(stage: str, model: str, resp, sec: float = 0.0, tier: str = "unknown") -> None:
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
        # このリクエストを実際に処理したキーの課金状態。KeyPool でのローテーション後に
        # 古いキーの状態のまま「実請求」と誤表示しないため、呼び出しごとに記録する
        # （2026-08-08：ローテーション後も起動時の tier のまま全額を実請求と誤表示するバグを修正）。
        "tier": tier,
        # どの区間を・どのキーで処理したか。チャンク別の明細表示と、キーごとの消費内訳に使う。
        "chunk": _CURRENT_CHUNK or None,
        "key_label": _CURRENT_KEY_LABEL or None,
    })


def _record_quality(name: str, duration_sec, text: str, ok: bool, reason: str,
                    attempts: int, flagged: bool = False, source: str = "api",
                    key_label: str = None) -> None:
    """1区間の文字起こし品質を記録する（一発合格／再試行回数／要確認）。

    `escalated` フィールドは 2026-08-08 のモデル単一化で常に False になったが、
    キーは残す。過去ログ（格上げが有効だった実行）と同じスキーマで読めるようにするため。

    `source` は "api"（この実行で文字起こしした）か "cache"（既存の <chunk>.txt を再利用した）。
    キャッシュ再利用も記録するのは、区間別の明細で全区間を並べたときに「表に出ていない区間」を
    作らないため——欠けていると、この実行で品質を確かめた区間だけが全体だと読めてしまう。"""
    chars = len(re.sub(r"\s", "", text or ""))
    QUALITY_LOG.append({
        "chunk": name,
        "duration_sec": round(duration_sec, 1) if duration_sec else None,
        "chars": chars,
        "chars_per_sec": round(chars / duration_sec, 2) if duration_sec else None,
        "attempts": attempts, "escalated": False,
        "ok": ok, "flagged": flagged, "reason": reason,
        "source": source,
        "key_label": key_label if key_label is not None else (_CURRENT_KEY_LABEL or None),
    })


def _pt_date() -> str:
    """太平洋時間の日付 YYYY-MM-DD（Google 無料枠のリセット基準＝PT 0時）。"""
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:                        # tzdata 無し等：夏時間(PDT=UTC-7)近似
        return (now_utc - _dt.timedelta(hours=7)).strftime("%Y-%m-%d")


def _bump_daily_tally(model: str, tokens: int, key_label: str = "?") -> None:
    """当ツールの1リクエストを PT 日付・「モデル × キー」別に積算する（他アプリの消費は含まない）。

    キー別に分けるのは、無料枠のクォータが「Cloud プロジェクト（＝キー） × モデル」単位で、
    キーをローテーションする以上「どの枠をどれだけ食ったか」がキーを跨いで合算されると
    意味を失うため（2026-08-08 にキー次元を追加。それ以前の行はモデル単位で入っている）。
    `_machine` にホスト名を記録するのは、後からファイルを見たときに
    「これはどの Mac の記録か・全体ではない」が分かるようにするため。"""
    try:
        DAILY_TALLY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if DAILY_TALLY_PATH.exists():
            data = json.loads(DAILY_TALLY_PATH.read_text(encoding="utf-8"))
        data["_machine"] = os.uname().nodename
        m = data.setdefault(_pt_date(), {}).setdefault(f"{model}@{key_label}",
                                                       {"requests": 0, "tokens": 0})
        m["requests"] += 1
        m["tokens"] += int(tokens or 0)
        days = sorted(k for k in data if not k.startswith("_"))
        for old in days[:-14]:               # 直近14日ぶんだけ保持
            data.pop(old, None)
        DAILY_TALLY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def write_daily_tally_report() -> None:
    """本日（PT基準）当ツールが消費した無料枠の目安を「モデル × キー」別に表示する。"""
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
    for bucket, m in sorted(today.items()):
        model = bucket.split("@", 1)[0]      # 2026-08-08 以前の行はキー無しの "モデル" だけ
        rpd = FREE_TIER_RPD.get(model)
        req = m["requests"]
        if rpd:
            print(f"  {bucket:<34}本日 {req} リクエスト / RPD目安 {rpd}（残り約 {max(0, rpd - req)}）"
                  f"  累計トークン {m['tokens']:,}")
        else:
            print(f"  {bucket:<34}本日 {req} リクエスト  累計トークン {m['tokens']:,}")
    print("  ※ 枠は「キー（Cloud プロジェクト）× モデル」単位。上の各行が1つの枠に対応する。")
    print("  ※ この集計は当ツール経由・この Mac だけのもの。他の Mac からの消費も同じ枠を食うが")
    print("     合算されない。RPD目安も Google が随時改定する。")
    print("     → 枠の判定にこの数字を使わないこと。実測は gemini-key-status.py、")
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
    日次上限(429 free tier / PerDay)・クレジット枯渇は、client が KeyPool かつ他に未使用の
    キーがあれば自動で切替えて同じリクエストを再試行する（試行回数は消費しない）。
    キーを使い切っても回復しない場合は QuotaExhaustedError で停止する
    （＝失敗を長引かせず、無料枠を無駄に消費しない）。"""
    attempt = 1
    while True:
        try:
            t0 = time.time()
            resp = client.models.generate_content(**kwargs)
            tier = client.current_tier() if isinstance(client, KeyPool) else _TIER_DECLARED
            # 消費を記録する直前に、実際に処理したキーのラベルを取り直す。ローテーションは
            # このループの中で起きるので、呼び出し前に控えた値では切替後の消費を旧キーに
            # 付け替えてしまう（tier を呼び出しごとに記録しているのと同じ理由）。
            global _CURRENT_KEY_LABEL
            if isinstance(client, KeyPool):
                _CURRENT_KEY_LABEL = client.label()
            _record_usage(stage, kwargs.get("model", ""), resp, time.time() - t0, tier=tier)
            um = getattr(resp, "usage_metadata", None)
            _bump_daily_tally(kwargs.get("model", ""),
                              getattr(um, "total_token_count", 0) if um else 0,
                              client.label() if isinstance(client, KeyPool) else "?")
            return resp
        except (errors.ServerError, errors.ClientError) as e:
            code = getattr(e, "code", None)
            if _is_tier_block(e):
                raise                        # モデル未提供。呼び出し側で格下げ
            if _quota_kind(e) in ("credits", "day") and isinstance(client, KeyPool) and client.rotate():
                continue                     # 新しいキーで同じリクエストを再試行
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
            attempt += 1


def _quality_summary() -> dict:
    """QUALITY_LOG を集計して品質サマリ dict を返す（区間なしなら空 dict）。"""
    if not QUALITY_LOG:
        return {}
    n = len(QUALITY_LOG)
    cached = sum(1 for q in QUALITY_LOG if q.get("source") == "cache")
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
    elif cached and not first_try:
        # 全区間がキャッシュ由来。この実行では1文字も文字起こししていないので、
        # 「一発合格」と書くと今回の成績のように読める。品質は満たしているが出所が違う。
        grade = "A（全区間がキャッシュ再利用・品質は充足）"
    else:
        grade = "A（全区間が一発合格）"
    return {
        "segments": n, "pass_first_try": first_try, "recovered": recovered,
        "flagged": len(flagged), "cached": cached,
        "covered_min": round(covered / 60, 1) if covered else None,
        "total_chars": chars,
        "chars_per_sec_avg": round(sum(cps) / len(cps), 2) if cps else None,
        "chars_per_sec_min": min(cps) if cps else None,
        "grade": grade,
        "flagged_chunks": [q["chunk"] for q in flagged],
    }


def _usage_by_chunk() -> dict:
    """チャンク名 → {calls, tokens, sec, keys} の集計（区間別明細の消費欄に使う）。"""
    agg: dict = {}
    for u in USAGE_LOG:
        c = u.get("chunk")
        if not c:
            continue
        d = agg.setdefault(c, {"calls": 0, "tokens": 0, "sec": 0.0, "keys": []})
        d["calls"] += 1
        d["tokens"] += u.get("total", 0) or 0
        d["sec"] += u.get("sec", 0.0) or 0.0
        kl = u.get("key_label")
        if kl and kl not in d["keys"]:
            d["keys"].append(kl)                # 区間の途中でキーが替わると複数入る
    return agg


def _disp_width(s: str) -> int:
    """端末上の表示幅（全角＝2）。Python の書式指定は文字数で数えるため、
    日本語が混ざる列（判定ラベル等）を `{:>12}` で揃えると必ずズレる。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int, right: bool = False) -> str:
    """表示幅を基準に空白で詰める（右寄せは right=True）。"""
    fill = " " * max(0, width - _disp_width(s))
    return fill + s if right else s + fill


def _verdict(q: dict) -> str:
    """1区間の判定ラベル。機械判定（check_quality）の結果であって、文字の正しさではない。"""
    if q.get("flagged"):
        return "要確認"
    if q.get("source") == "cache":
        return "キャッシュ"
    if q.get("attempts", 1) > 1:
        return f"回復({q['attempts']}回)"
    return "合格"


def write_quality_report():
    """文字起こし品質のサマリを表示する（全体の総合評価と、区間ごとの明細）。"""
    q = _quality_summary()
    if not q:
        return
    print("\n── 文字起こし品質 ──")
    print(f"  総合評価: {q['grade']}")
    print(f"  区間数 {q['segments']} ／ 一発合格 {q['pass_first_try']} ／ "
          f"再試行・格上げで回復 {q['recovered']} ／ 要確認 {q['flagged']} ／ "
          f"キャッシュ再利用 {q.get('cached', 0)}")
    if q["chars_per_sec_avg"] is not None:
        print(f"  発話密度 平均 {q['chars_per_sec_avg']} 字/秒"
              f"（最小 {q['chars_per_sec_min']} 字/秒。低いほど途切れの疑い）")
    if q["covered_min"] is not None:
        print(f"  処理カバレッジ 約 {q['covered_min']} 分 ／ 総文字数 {q['total_chars']:,} 字")

    # 区間別の明細。全体の総合評価だけだと、どの区間が薄いのか・どこにトークンを
    # 使ったのかが分からない。発話密度は区間ごとにばらつき、低い区間が途切れの候補になる。
    by_chunk = _usage_by_chunk()
    print("\n  区間別（判定は check_quality による機械判定。**文字の正しさは見ていない**）")
    print("    " + _pad("区間", 28) + _pad("尺", 8, True) + _pad("文字数", 10, True)
          + _pad("字/秒", 8, True) + _pad("判定", 12, True) + "   消費")
    for r in QUALITY_LOG:
        name = r["chunk"] or "?"
        short = name if _disp_width(name) <= 27 else "…" + name[-26:]
        dur = f"{r['duration_sec'] / 60:.1f}分" if r.get("duration_sec") else "—"
        cps = f"{r['chars_per_sec']}" if r.get("chars_per_sec") else "—"
        u = by_chunk.get(name)
        if u:
            keys = f" [{'→'.join(u['keys'])}]" if u["keys"] else ""
            spend = f"{u['tokens']:,} tok / {u['sec']:.0f}秒 / {u['calls']}回{keys}"
        else:
            spend = "—（API 呼び出しなし）"
        print("    " + _pad(short, 28) + _pad(dur, 8, True) + _pad(f"{r['chars']:,}", 10, True)
              + _pad(cps, 8, True) + _pad(_verdict(r), 12, True) + "   " + spend)
        if r.get("reason"):
            print(f"      └ {r['reason']}")
    if q["flagged_chunks"]:
        print(f"  ⚠ 要確認区間: {', '.join(q['flagged_chunks'])}")


def _call_cost(u: dict) -> float:
    """1リクエストの概算課金額（USD）。音声／テキストを別単価で、200k 超は上位帯の単価で計算する。
    階層はリクエスト単位で決まるため、モデル別に合算してから掛けると誤る（合算すると全リクエストが
    200k 超に見える）。ここで1件ずつ計算し、呼び出し側で足し上げる。"""
    pr = _rate(u["model"], u["prompt"], PRICING)
    if not pr:
        return 0.0
    out_tokens = u.get("candidates", 0) + u.get("thoughts", 0)
    return (u.get("prompt_audio", 0) / 1e6 * pr["in_audio"]
            + u.get("prompt_text", u["prompt"]) / 1e6 * pr["in"]
            + out_tokens / 1e6 * pr["out"])


def _thinking_cost(u: dict) -> float:
    """1リクエストのうち thinking（思考）トークンぶんの課金額（USD）。

    thinking は `candidates_token_count` に含まれないが**出力単価でそのまま課金される**。
    2026-08-04 の commit 4e77f4c まで記録すらしておらず、7月中の消費が丸ごと不可視だった。
    実測では当時の格上げ先 gemini-3.5-flash で出力の10〜25倍の thinking が発生し、
    1件の会議（91分）で総額 690 円のうち 519 円（73%）を占めた。lite 系は thinking を出さない。
    現在の唯一のモデルは lite なので 0 になるはずだが、項目自体は残す——値が 0 でないことが
    「想定外のモデルで走った」という異常の検知になるため（総額に混ぜると同じ誤りを繰り返す）。"""
    pr = _rate(u["model"], u["prompt"], PRICING)
    if not pr:
        return 0.0
    return u.get("thoughts", 0) / 1e6 * pr["out"]


def _job_id(stem: str, out_dir: Path) -> str:
    """案件名を安定した8桁の識別子に変換し、実名との対応を端末ローカルの地図に記録する。

    コストログは git 管理下に置いて複数マシンで共有するため、**案件名（stem）をそのまま
    書かない**。案件名は音声ファイル名に由来し、会議名や人名になる。そのまま履歴に残すと、
    健康情報・人事情報に触れる会議のようにコミットできないものが混ざり、後から気づいても
    履歴の書き換えが必要になる（実際に起きた）。対応表 JOB_MAP_PATH は `.local.json` で
    gitignore 対象にし、実名は各マシンの中だけで解決する。"""
    jid = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:8]
    try:
        JOB_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        m = {}
        if JOB_MAP_PATH.exists():
            m = json.loads(JOB_MAP_PATH.read_text(encoding="utf-8"))
        m[jid] = {"job": stem, "out_dir": str(out_dir)}
        JOB_MAP_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass                                  # 地図が書けなくてもコストログは残す
    return jid


def _append_cost_log(report: dict, stem: str, out_dir: Path) -> None:
    """実行1回ぶんのコスト明細を COST_LOG_PATH に1行追記する（追記専用・履歴を消さない）。

    stage 別・thinking の内訳・格上げ回数まで残すのは、「どの案件のどの工程に
    いくら使ったか」を成果物の移動・削除に関係なく後から辿れるようにするため。
    案件名は `job_id`（`_job_id` 参照）に置き換えて書く。"""
    try:
        q = report.get("quality") or {}
        calls = report.get("calls") or []
        stages = [{"stage": r["stage"], "model": r["model"], "calls": r["calls"],
                   "tokens": r["total"], "thoughts": r.get("thoughts", 0),
                   "usd": r["est_usd"]} for r in report.get("by_stage", [])]
        th_usd = sum(_thinking_cost(u) for u in calls)
        usd = report.get("est_usd_approx", 0.0)
        row = {
            "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pt_date": _pt_date(),            # 無料枠 RPD のリセット基準に揃える
            "machine": os.uname().nodename,   # 集計はマシン別。合算は閲覧側で行う
            "job_id": _job_id(stem, out_dir),  # 実名は JOB_MAP_PATH（gitignore）側にだけ置く
            "billing_tier": report.get("billing_tier"),
            "tier_source": report.get("tier_source"),   # declared=宣言 / 欠落や measured=実測（〜2026-08-08）
            "billed": report.get("billed"),
            "api_key": report.get("api_key"),
            "usd": round(usd, 4),
            "jpy": report.get("est_jpy_approx"),
            # 実請求ぶん／無料枠ぶんを分けて残す（KeyPool のローテーションで1回の実行内に
            # 課金キーと無料枠キーが混在し得るため、行全体を billed 一択で丸めると
            # 集計側〔gemini-cost-report.py〕が混在ぶんを誤って実請求扱いする）。
            "billed_jpy": report.get("billed_jpy_approx"),
            "free_jpy": report.get("free_jpy_approx"),
            "thinking_usd": round(th_usd, 4),
            "thinking_jpy": round(th_usd * 155),
            "thinking_pct": round(th_usd / usd * 100) if usd else 0,
            "tokens": {
                "total": report.get("total_tokens", 0),
                "prompt": sum(u.get("prompt", 0) for u in calls),
                "prompt_audio": sum(u.get("prompt_audio", 0) for u in calls),
                "candidates": sum(u.get("candidates", 0) for u in calls),
                "thoughts": sum(u.get("thoughts", 0) for u in calls),
            },
            "requests": len(calls),
            "audio_min": q.get("covered_min"),
            "grade": q.get("grade"),
            "segments": q.get("segments"),
            "escalated": sum(1 for d in report.get("quality_detail", []) if d.get("escalated")),
            "retried": sum(1 for d in report.get("quality_detail", []) if (d.get("attempts") or 1) > 1),
            "flagged": q.get("flagged"),
            "elapsed_sec": report.get("elapsed_sec"),
            "api_sec": report.get("api_sec"),
            "thoughts_recorded": True,        # False の行は 2026-08-04 以前で thinking が未計上＝下限値
            "stages": stages,                 # 工程別の内訳（どのパートに使ったか）
        }
        COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with COST_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, ValueError, KeyError):
        pass                                  # ログ追記の失敗で本処理を落とさない


def _load_key_accounts() -> dict:
    """ラベル → {account, project, plan} の対応表を読む（無ければ空 dict）。

    キーのラベル（NHO / SAITOLA 等）だけでは、どの Google アカウント・どの Cloud
    プロジェクトの枠を消費したのかが実行ログから読み取れない。**この対応表は任意**で、
    無ければラベルと指紋の表示に留める（推測でアカウント名を補完しない）。"""
    try:
        data = json.loads(KEY_ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k).lower(): v for k, v in data.items() if isinstance(v, dict)}


def _tier_conflicts() -> list:
    """「無料枠と宣言したキー」が課金キーとして記録されていないかを照合し、矛盾を返す。

    課金状態の実測は 2026-08-08 に廃止し、GEMINI_API_KEY_POOL への記載を無料枠の宣言として
    扱う運用になった。宣言が誤っていると**黙って実請求が始まる**（レポートは「実請求なし」と
    表示し続ける）。過去の実測記録が残っている指紋については、ここで食い違いを拾える。"""
    if _TIER_DECLARED != "free" or not _KEY_FINGERPRINTS:
        return []
    try:
        known = json.loads(KEY_TIER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for label, fp in _KEY_FINGERPRINTS.items():
        rec = known.get(fp) or {}
        if rec.get("tier") == "paid":
            out.append((label, fp, rec.get("checked", "?")))
    return out


def _key_breakdown() -> dict:
    """キーのラベル → {calls, tokens} の集計（どの枠をどれだけ食ったか）。"""
    agg: dict = {}
    for u in USAGE_LOG:
        d = agg.setdefault(u.get("key_label") or "?", {"calls": 0, "tokens": 0})
        d["calls"] += 1
        d["tokens"] += u.get("total", 0) or 0
    return agg


def write_account_report():
    """どのアカウント・どのキー・どのプランで実行したかを表示する。

    無料枠は利用規約上、入力と出力を人間のレビュアーが読み得る。**どのアカウントの枠で
    処理したか**は、後から「この音声をどこに出したか」を辿る唯一の手掛かりになるので、
    実行のたびに残す。キーの値は表示しない（指紋のみ）。"""
    accounts = _load_key_accounts()
    breakdown = _key_breakdown()
    used_labels = [l for l in breakdown if l and l != "?"]
    print("\n── 使用したアカウントとプラン ──")
    if not USAGE_LOG:
        # 全区間がキャッシュ再利用だった実行。ここでキーを「使用した」ように書くと、
        # 音声を送っていないのに送ったと読める（無料枠に何を出したかの記録が歪む）。
        print("  この実行では Gemini API を呼んでいない（全区間がキャッシュ再利用）。"
              "送信・消費・課金はいずれもゼロ。")
        print(f"  呼び出しがあれば使われるキー: {_API_KEY_DESC}")
        return
    if not used_labels:
        print(f"  APIキー   {_API_KEY_DESC}")
    for label in used_labels:
        fp = _KEY_FINGERPRINTS.get(label, "?")
        info = accounts.get(label.lower(), {})
        acct = info.get("account")
        proj = info.get("project")
        who = acct or "（アカウント未登録）"
        tail = f" ／ Cloud プロジェクト {proj}" if proj else ""
        b = breakdown[label]
        print(f"  キー［{label}］指紋 {fp}　→　{who}{tail}")
        print(f"      この実行での消費: {b['calls']}回 / {b['tokens']:,} トークン")
    if not accounts:
        print(f"  ※ ラベルとアカウントの対応表が未設定（{KEY_ACCOUNTS_PATH}）。"
              "作るとこの欄にアカウント名・プロジェクト名が出る。")
    plan = {"free": "無料枠（Free tier）", "unknown": "不明"}.get(_TIER_DECLARED, _TIER_DECLARED)
    print(f"  プラン    {plan}")
    if _TIER_DECLARED == "free":
        print("            ※ GEMINI_API_KEY_POOL への記載にもとづく**宣言**であって実測ではない。"
              "キーの Cloud プロジェクトで課金を有効にすると、この表示のまま実請求に変わる。")
    for label, fp, checked in _tier_conflicts():
        print(f"  ⚠ キー［{label}］（指紋 {fp}）は課金キーとして記録されている"
              f"（{KEY_TIER_PATH.name}・{checked} 時点）。無料枠の宣言と矛盾する。"
              " https://aistudio.google.com/billing で確認すること。")


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
    # 課金状態は実測しない（プローブは 2026-08-08 廃止）。GEMINI_API_KEY_POOL に載せたキーは
    # 無料枠であるという利用者の宣言として扱い、tier_source で実測でないことを明示する。
    # 過去ログには実測値（'paid'/'free'/'mixed'）の行が残るので、集計側は両方を読めること。
    tier_summary = _TIER_DECLARED if _TIER_DECLARED in ("free", "unknown") else "unknown"
    billed = False if tier_summary == "free" else None
    free_usd = est_cost if tier_summary == "free" else 0.0
    report = {"total_tokens": total_tokens, "est_usd_approx": round(est_cost, 4),
              "est_jpy_approx": round(est_cost * 155),
              "billing_tier": tier_summary,          # free=請求なし（宣言） / unknown=判別しない
              "tier_source": "declared",             # 実測ではない。'measured' の行は 2026-08-08 以前
              "billed": billed,
              "billed_usd_approx": 0.0,              # 課金キーはプールに入れない運用
              "billed_jpy_approx": 0,
              "free_jpy_approx": round(free_usd * 155),  # 無料枠ぶん（請求なし。課金なら相当する額の参考値）
              "api_key": _API_KEY_DESC,   # どのキーで消費したかを後から追えるようにする（値は含まない）
              # キー別の消費内訳とアカウント。**キーの値は入れない**（ラベル・指紋・
              # 対応表に登録されたアカウント名だけ）。無料枠は人間のレビュアーが読み得るため、
              # 「どのアカウントの枠にこの音声を出したか」を成果物側にも残す。
              "keys_used": [
                  {"label": lb, "fingerprint": _KEY_FINGERPRINTS.get(lb, "?"),
                   "account": (_load_key_accounts().get(lb.lower(), {}) or {}).get("account"),
                   "project": (_load_key_accounts().get(lb.lower(), {}) or {}).get("project"),
                   **v}
                  for lb, v in _key_breakdown().items() if lb and lb != "?"],
              "tier_conflicts": [{"label": lb, "fingerprint": fp, "checked": ck}
                                 for lb, fp, ck in _tier_conflicts()],
              "no_free_tier_models": billable,
              "elapsed_sec": round(elapsed, 1), "elapsed_human": _fmt_dur(elapsed),
              "api_sec": round(api_sec, 1),
              "note": "est_usd は公式単価（音声入力単価・200k超の階層を反映）による概算。"
                      "billing_tier は実測ではなく GEMINI_API_KEY_POOL による宣言（tier_source=declared）。"
                      "キーの Cloud プロジェクトで課金を有効にすると、この表示のまま実請求に変わる。"
                      "elapsed_sec は本コマンドの総経過時間、api_sec は Gemini 応答待ちの合計。",
              "quality": quality, "by_stage": rows,
              "quality_detail": QUALITY_LOG, "calls": USAGE_LOG}
    path = out_dir / f"{stem}_usage.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    models_used = sorted({r["model"] for r in rows})
    write_account_report()
    print("\n── 使用モデル ──")
    if models_used:
        print(f"  {', '.join(models_used)}　／　APIキーの経路: {_API_KEY_DESC}")
    else:
        print("  （この実行では Gemini を呼んでいない。キャッシュ済みの文字起こしを再利用した）")
    print("\n── トークン消費（Gemini API） ──")
    for r in rows:
        # thinking は出力単価で課金されるので out と足さずに別項目で見せる（混ぜると主因が隠れる）
        print(f"  {r['stage']:<14}{r['model']:<22}calls={r['calls']:>2}  "
              f"total={r['total']:>9,}  (in={r['prompt']:,}〔音声 {r['prompt_audio']:,}〕"
              f" / out={r['candidates']:,} / 思考={r['thoughts']:,})  "
              f"{r['sec']:>6.1f}秒  ~${r['est_usd']}")
    jpy = round(est_cost * 155)
    if tier_summary == "free":
        print(f"  合計 {total_tokens:,} トークン ／ **実請求なし**（無料枠。課金なら {jpy:,} 円相当）")
        print("  ※ GEMINI_API_KEY_POOL のキーを無料枠として扱っている（実測はしない）。RPD の上限はある。")
        print("  ※ 無料枠は利用規約上、人間のレビュアーが入出力を読む（機密区分の判断はこの表示ではなく運用側で行う）。")
    else:
        print(f"  合計 {total_tokens:,} トークン ／ 概算 ${round(est_cost, 4)}（約 {jpy:,} 円）")
        print("  ※ プールが宣言されていないため、実請求か無料枠内かは判別しない。"
              "GEMINI_API_KEY_POOL で無料枠キーを明示すること。")
    if billable:
        print(f"  ⚠ 無料枠が無いモデルを使用: {', '.join(billable)}（無料枠キーでも 429 になる）")
    # thinking の寄与を独立して出す。ここが総額の過半になる実行があり、混ぜると原因が見えない
    th_usd = sum(_thinking_cost(u) for u in USAGE_LOG)
    th_tok = sum(u.get("thoughts", 0) for u in USAGE_LOG)
    if th_tok:
        pct = round(th_usd / est_cost * 100) if est_cost else 0
        print(f"  うち thinking（思考トークン）: {th_tok:,} トークン ／ 約 {round(th_usd * 155):,} 円（総額の {pct}%）")
        if pct >= 40:
            print("  ⚠ thinking が総額の4割超。既定の lite は thinking を出さないはずなので、"
                  "--model で lite 以外を指定していないか確認する。")
    print(f"\n── 所要時間 ──")
    print(f"  総経過 {_fmt_dur(elapsed)}（うち Gemini 応答待ち {_fmt_dur(api_sec)}）")
    write_quality_report()
    _append_cost_log(report, stem, out_dir)
    print(f"\n── 累積コストログ ──\n  {COST_LOG_PATH} に追記した"
          f"（閲覧: {Path(__file__).parent / 'gemini-cost-report.py'}）")
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


# ── 後処理（Step 4）の生成ルール ──────────────────────────────────
# ⚠ 以下4つのプロンプト定数は、このスクリプトからは送信しない（2026-08-08 に Gemini 呼び出しを
# 削除した）。**ケバ取り・凝縮・数値補完を行う Claude が読むための、生成ルールの正本**である。
# SKILL.md Step 4 はここを読めと指示しており、規則を SKILL.md 側へ写していない
# （同じ規則が2箇所にあるとズレるため）。**削除・要約してはいけない。**
# 各ルールには実測の根拠が入っている（例：凝縮版の金額・台数の保持率 35% → 70%）。
VERBATIM_PROMPT = """以下の会議文字起こしから、フィラー（えー、あのー、そのー、えっと、あの、まあ（文頭の意味のない使用）、なんか（意味のない使用）など）と明らかな言い淀み（同じ語の直後の繰り返し）のみを除去してください。

ルール：
- フィラーと言い淀み以外の内容は一切省略しない
- 話者ラベル（「話者A:」等、実名や確度記号〔◎〕〔○〕〔△〕〔？〕付きに置換済みの場合はその表記）と発言順序・タイムスタンプを保持する
- 各発言は「話者ラベル: 発言内容」の1行形式を維持する
- 同じ文や段落を繰り返さない
- 出力は処理後のテキストのみ（説明文不要）

---
{text}"""

# 凝縮版のプロンプト。「凝縮」を主タスクにして圧縮系のルールを並べると、モデルは
# 短くすることを最適化し、埋め込まれた「省略厳禁」の1行を無視する。2026-08-04 の実測では
# 予算面談の要約から 5000万・3800万・2500万・700万・500万・200万・123台 が丸ごと落ち、
# 金額・台数の保持率は 35% しかなかった。そこで
#   (1) 制約を冒頭の独立ブロックに出して、書き方のルールと分離する
#   (2) 何が「事実」なのかを分類して例示する（金額・台数・人数・期限・固有名詞…）
#   (3) 概数へのぼかしを明示的に禁じる
#   (4) 書いたあとに照合する手順を課す
# の4点に作り替えたところ、同じモデルのままで 70% まで回復した。
# 残りは機械的な検査（--check-numbers）で拾い、SUMMARY_REPAIR_PROMPT の作法に従って埋める。
CONDENSED_PROMPT = """以下の会議文字起こしから議事録要約を作成してください。

## 最優先の制約：事実は1つも落とさない

削ってよいのは**言い回し**だけです。**事実は削れません。** 次に挙げるものは、入力に現れたら必ず出力にも原文の値のまま含めてください。

- **金額**（例: 5000万、3800万、200万、20円）
- **数量・台数・人数**（例: 123台、8台、89人、5名）
- **日付・期限・年度・時刻**（例: 2026年、4月、20日、17時）
- **割合・件数**（例: 60件、2件）
- **固有名詞**（人名・組織名・部署名・製品名・システム名・勘定科目名）
- **意思決定と、その担当者・期限**

**概数への置き換えを禁止します。** 「数百万円」「複数台」「数名」のようにぼかさず、原文の数値をそのまま書いてください。数値が話題の中心（予算・契約・人員）である会議では、数値の脱落は要約の失敗とみなします。

## 手順

1. まず入力を通読し、上の分類に当てはまる値をすべて拾い出す。
2. そのうえで本文を書く。
3. **書き終えたら、1で拾った値が本文に含まれているか照合する。漏れていれば本文に加える。**

## 書き方

- 発言の本質（事実・意見・意思決定・背景）を保ちつつ、口語特有の繰り返し・言い直し・相槌・冗長な言い回しを削ぎ落とし、事務的で洗練された文章に再構成する
- 断片的な発言を文脈ごとに一貫性のある段落として統合する（発言者が混在してよい）
- 話題の切り替わりごとに ## レベルの小見出しを付ける
- 話者名は段落冒頭または文中で自然に示す。確度記号は簡略表記にする：**〔◎〕（確実）は付けない**（例「古賀部長より：…」）、〔○〕は「○」、〔△〕は「△」、〔？〕は「？」を氏名直後に付す（例「金子○より：…」「話者不明？」）
- 意味のない相槌のみの行は削除する
- 同じ内容を繰り返さない
- 出力はMarkdownのみ（説明文・前置き・「以上です」等は不要）

---
{text}"""

MERGE_PROMPT = """以下は同一会議を時系列の区間ごとに凝縮した要約の連結です。全体を1本の議事録要約に統合してください。

## 最優先の制約：統合は圧縮ではない

**入力に現れる金額・数量・台数・人数・日付・期限・年度・時刻・割合・固有名詞・意思決定は、1つ残らず出力に引き継いでください。** 概数へのぼかし（「数百万円」「複数台」）を禁止します。区間をまたぐ重複を1つにまとめることは求めますが、**事実そのものを減らすことは求めていません。**

統合後に、入力側に出てくる数値・固有名詞が出力に残っているか照合し、漏れていれば加えてください。

## 書き方

- 区間をまたぐ重複を排除し、話題ごとに ## 小見出しで再構成する
- 時系列と論理の流れを保つ
- 確度記号は簡略表記（〔◎〕は付けない、〔○〕→「○」、〔△〕→「△」、〔？〕→「？」）
- 出力はMarkdownのみ（説明文不要）

---
{text}"""

SUMMARY_REPAIR_PROMPT = """以下は会議の議事録要約と、その元になった文字起こしから抽出した「要約に含まれていない数値」の一覧です。

要約に抜けている数値を、**原文の文脈に沿って要約の適切な段落に挿入**してください。

ルール：
- **既存の記述を書き換えない。** 構成・小見出し・語り口はそのまま保ち、抜けている数値とその文脈だけを最小限の加筆で補う
- 原文の値をそのまま使う（概数へのぼかし禁止）
- **文脈が判然としない値、聞き取り誤りと判断できる値は無理に入れない。** 入れられなかった値があってもよい
- 出力は補完後の要約Markdown全文のみ（説明文・差分・前置きは不要）

## 要約

{summary}

## 要約に含まれていない数値と、原文での出現箇所

{missing}
"""


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
    attempt = 1
    while up is None:
        try:
            with open(path, "rb") as fh:
                up = client.files.upload(
                    file=fh, config=types.UploadFileConfig(mime_type=mime, display_name=path.name))
        except (errors.ClientError, errors.ServerError) as e:
            if _quota_kind(e) in ("credits", "day") and isinstance(client, KeyPool) and client.rotate():
                continue                     # 新しいキーで同じファイルを再アップロード
            _raise_if_hard_quota(e)
            code = getattr(e, "code", None)
            if code not in (429, 500, 503) or attempt == 3:
                raise
            wait = min(60, (_retry_delay_seconds(e) or 15) + 2)
            print(f"\n  アップロード一時エラー（{code}）。{wait}秒後に再試行 ({attempt}/3)",
                  end="", flush=True)
            time.sleep(wait)
            attempt += 1
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


def transcribe_file(client, path: Path, prompt: str, model: str, stage: str = "transcribe") -> str:
    global _CURRENT_CHUNK, _CURRENT_KEY_LABEL
    _CURRENT_CHUNK = path.name
    _CURRENT_KEY_LABEL = client.label() if isinstance(client, KeyPool) else ""
    print(f"  [{model}] {path.name} アップロード中", end="", flush=True)
    f = _upload(client, path)
    print(" → 文字起こし中...", end="", flush=True)
    try:
        resp = generate_with_retry(
            client, stage, model=model, contents=[prompt, f],
            config=types.GenerateContentConfig(temperature=0.0))
    except errors.ClientError as e:
        client.files.delete(name=f.name)
        # モデルがこのキーの枠で使えない（limit: 0）場合、以前は別モデルへ格下げしていたが、
        # 使えるモデルを1つに固定した以上、逃げ先はモデルではなく別のキーしかない。
        # generate_with_retry が既にキー切替を試みたうえでここへ来ているので、
        # 黙って別モデルに落とさず、原因を明示して止める。
        if _is_tier_block(e):
            raise QuotaExhaustedError(
                f"{model} がプール内のどのキーの枠でも使えません（quota limit: 0）。"
                "キーが紐づく Cloud プロジェクトでこのモデルが提供されているかを "
                "https://aistudio.google.com/rate-limit で確認してください。") from e
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


def transcribe_with_recovery(client, path: Path, prompt: str, model: str,
                             stage: str = "transcribe", duration_sec: float = None,
                             depth: int = 0) -> str:
    """1チャンクを文字起こしし、品質不良なら再試行→再分割で回復する。

    かつては再試行の2回目で上位モデルへ格上げしていたが、使えるモデルを1つに固定したため
    廃止した（2026-08-08）。格上げが有効だったのは 3.5-flash-lite → 3.5-flash の組だけで、
    その 3.5-flash は thinking が出力の10〜25倍出てコストの主役になっていた（実測で総額の
    41%、格上げが起きた実行では 64〜73%）。同じ失敗モードは再試行1回でも回復している。"""
    min_chars = int(duration_sec * MIN_CHARS_PER_SEC) if duration_sec else None
    max_chars = int(duration_sec * MAX_CHARS_PER_SEC) if duration_sec else None
    attempts = []
    for attempt in range(2):                       # 初回＋再試行1
        text = normalize_lines(collapse_loops(transcribe_file(client, path, prompt, model, stage)))
        ok, reason = check_quality(text, min_chars=min_chars, max_chars=max_chars,
                                   duration_sec=duration_sec)
        if ok:
            _record_quality(path.name, duration_sec, text, True, "", attempt + 1)
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
                transcribe_with_recovery(client, c, prompt, model, stage, d, depth + 1),
                offset))
            offset += d or 0
        return "\n".join(out)
    text, reason = max(attempts, key=lambda a: len(a[0]))
    print(f"    ✗ 品質を確保できず。該当区間に注記を付与: {reason}")
    _record_quality(path.name, duration_sec, text, False, reason, 2, flagged=True)
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
                # 再利用した区間も品質ログに残す（API を呼んでいないので attempts=0）。
                # 残さないと区間別の明細に穴が空き、この実行で取り直した区間だけが
                # 全体であるかのように読める。
                _record_quality(chunk.name, d, cached, True, "", 0,
                                source="cache", key_label=None)
                parts.append(f"## Part {i + 1} — {chunk.name}\n\n{cached}")
                continue
            print(f"[{i + 1}/{n}] キャッシュを破棄して取り直し（{why}）")
        print(f"[{i + 1}/{n}]", end=" ")
        text = transcribe_with_recovery(client, chunk, prompt, model, "transcribe", d)
        cache.write_text(text, encoding="utf-8")      # チェックポイント保存
        parts.append(f"## Part {i + 1} — {chunk.name}\n\n{text}")
    print(f"カバレッジ: 約 {covered/60:.1f} 分ぶんのチャンクを処理")
    return "\n\n---\n\n".join(parts)


# ── 凝縮版の数値チェック（API 不要・無課金。--check-numbers から使う） ──────
# 凝縮版から落ちてはいけない数値の型。金額・台数に絞るのは、これが議題の核になりやすく
# （予算・契約・人員）、かつ「8階」「1回」のような些末値と機械的に区別できるため。
# 対象を広げると誤検知が増えて、修復パスが ASR の誤認識まで本文へ運び込む。
MATERIAL_NUM_RE = re.compile(r"\d[\d,．\.]*\s*(?:円|万|億|台)")


def _norm_num(s: str) -> str:
    """数値表現の表記ゆれ（空白・桁区切り）を吸収する。"""
    return re.sub(r"[\s,]", "", s)


def _missing_material_numbers(src: str, summary: str) -> dict:
    """原文にあって凝縮版に無い金額・台数を {正規化値: (原表記, 原文の該当箇所)} で返す。
    原文の行を添えるのは、値だけ渡すとモデルが置き場所を作れず捏造しかねないため。"""
    have = {_norm_num(x) for x in MATERIAL_NUM_RE.findall(summary)}
    lines = src.splitlines()
    out: dict = {}
    for i, line in enumerate(lines):
        for m in MATERIAL_NUM_RE.findall(line):
            k = _norm_num(m)
            if k in have or k in out:
                continue
            out[k] = (m, "\n".join(lines[max(0, i - 1):i + 2]))
    return out


def _fabricated_numbers(src: str, summary: str) -> list:
    """凝縮版にあって原文に無い金額・台数。合計値などモデルが計算した数の可能性があるので
    自動では消さず、人が確かめられるよう警告だけ出す。"""
    s = {_norm_num(x) for x in MATERIAL_NUM_RE.findall(src)}
    return sorted({_norm_num(x) for x in MATERIAL_NUM_RE.findall(summary)} - s)


# 数値の補完（repair_summary）・確度記号の簡略化（_summary_marker_style）・
# タイトル生成（derive_title）は Gemini 呼び出しだったため 2026-08-08 に削除した。
# いずれも Claude 側の Step 4 で行う。規則の正本は SUMMARY_REPAIR_PROMPT（補完の作法）と
# SKILL.md（確度記号の簡略化・既定名からのリネーム）。


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


def _pool_entries_from_declaration() -> list:
    """環境変数 GEMINI_API_KEY_POOL が指定されていれば、そのラベル順にキーを組む。

    無料枠のクォータは「Cloud プロジェクト × モデル」単位（2026-08-08 実測）で、使えるモデルは
    1つに固定した。したがって枠を増やす唯一の手段はアカウント（キー）を増やすことになる。

    自動検出ではなく明示リストにした理由（齋藤指示 2026-08-08）:
      1. **課金キーを混ぜない。** 課金状態の実測プローブを廃止したため、課金キーが紛れ込んでも
         気づけない。GEMINI_API_KEY_POOL に載せたキーは「無料枠である」という利用者の宣言として扱う。
      2. **順序を決められる。** 自動検出は環境変数名のアルファベット順で、しかも GEMINI_API_KEY
         （＝既定キー）が先頭に来ていた。既定キーが課金キーだと、無料枠に手を付ける前に課金が始まる。

    書式: GEMINI_API_KEY_POOL="NHO SAITOLA"（空白またはカンマ区切り。GEMINI_API_KEY_<ラベル> を参照）
    """
    raw = os.environ.get("GEMINI_API_KEY_POOL", "").strip()
    if not raw:
        return []
    entries, seen, missing = [], set(), []
    for label in re.split(r"[,\s]+", raw):
        if not label:
            continue
        val = os.environ.get(f"GEMINI_API_KEY_{label.upper()}", "").strip()
        if not val:
            missing.append(label)
            continue
        fp = _key_fingerprint(val)
        if fp in seen:                       # 同じキーを二重に数えない（枠は共通）
            continue
        seen.add(fp)
        entries.append((label.lower(), val))
    if missing:
        print(f"  ⚠ GEMINI_API_KEY_POOL に指定されたが環境変数が未設定: {', '.join(missing)}"
              f"（GEMINI_API_KEY_<ラベル> を設定してください）")
    return entries


def _discover_pool_entries(primary_key: str, primary_label: str) -> list:
    """GEMINI_API_KEY_POOL が未設定のときのフォールバック。primary キーに加え、
    GEMINI_API_KEY_<LABEL> 環境変数から見つかる他アカウントのキーを集める。
    値が同じキーは1つにまとめる（同じアカウントを二重に数えない）。

    ⚠ この経路は課金キーを排除できない（プローブ廃止で課金状態を測れないため）。
    複数キーを持つ環境では GEMINI_API_KEY_POOL で明示すること。"""
    entries = []
    seen = set()
    if primary_key:
        entries.append((primary_label, primary_key))
        seen.add(_key_fingerprint(primary_key))
    for name in sorted(os.environ):
        if not name.startswith("GEMINI_API_KEY_") or name == "GEMINI_API_KEY_POOL":
            continue
        val = os.environ.get(name, "").strip()
        if not val:
            continue
        fp = _key_fingerprint(val)
        if fp in seen:
            continue
        seen.add(fp)
        entries.append((name[len("GEMINI_API_KEY_"):].lower(), val))
    return entries


class KeyPool:
    """複数アカウントのAPIキーを保持し、無料枠の日次上限・前払いクレジット枯渇に
    達したキーを自動でスキップして次のキーへ切替える。generate_with_retry / _upload の
    呼び出し側からは genai.Client と同じ `.models` / `.files` インターフェースに見える。"""

    def __init__(self, entries: list, declared_tier: str = "unknown"):
        if not entries:
            raise ValueError("キーが1つもありません")
        self.entries = entries              # [(label, key), ...]
        self.idx = 0
        self._client = None
        self._exhausted = set()             # 枠上限・枯渇済みの指紋
        # 'free'（GEMINI_API_KEY_POOL による宣言）または 'unknown'（フォールバック経路）。
        # 実測プローブは 2026-08-08 に廃止したので、ここに 'paid' が入ることはない。
        self.declared_tier = declared_tier

    def fingerprint(self, i: int = None) -> str:
        _, key = self.entries[self.idx if i is None else i]
        return _key_fingerprint(key)

    def label(self, i: int = None) -> str:
        return self.entries[self.idx if i is None else i][0]

    def current_tier(self) -> str:
        """このプールのキーの課金状態（宣言値）。キーごとに変えないのは、
        GEMINI_API_KEY_POOL が「ここに載せるのは無料枠キーだけ」という宣言だからで、
        1本でも課金キーが混ざれば宣言そのものが誤っていることになる。"""
        return self.declared_tier

    @property
    def current(self):
        if self._client is None:
            self._client = genai.Client(api_key=self.entries[self.idx][1])
        return self._client

    @property
    def models(self):
        return self.current.models

    @property
    def files(self):
        return self.current.files

    def rotate(self) -> bool:
        """現在のキーを枯渇扱いにし、未使用の次のキーへ切替える。切替できれば True。
        全キーを使い切っていれば False（呼び出し側は通常の QuotaExhaustedError へ進む）。"""
        self._exhausted.add(self.fingerprint())
        old_label = self.label()
        for step in range(1, len(self.entries)):
            cand = (self.idx + step) % len(self.entries)
            if self.entries[cand][1] and _key_fingerprint(self.entries[cand][1]) not in self._exhausted:
                self.idx = cand
                self._client = None
                global _API_KEY_DESC
                _API_KEY_DESC += f" → {self.label()}（指紋 {self.fingerprint()}）"
                print(f"\n  ⚠ キー［{old_label}］が枠上限/クレジット枯渇 → キー［{self.label()}］へ切替")
                return True
        return False


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
    # モデルは選択肢を PRICING に載っているものだけに限定する。ここを自由入力にすると、
    # 単価表に無いモデルが指定されてコスト計算が黙って 0 円になる。
    # 実質 gemini-3.5-flash-lite の1択（他モデルを外した根拠はファイル冒頭の定数コメント）。
    p.add_argument("--model", default=TRANSCRIBE_MODEL, choices=sorted(PRICING),
                   help=f"文字起こしモデル。デフォルト: {TRANSCRIBE_MODEL}")
    p.add_argument("--rpd", type=int, metavar="N",
                   help="無料枠の1日リクエスト数（RPD）目安を上書き（本日消費表示用。既定 250）")
    p.add_argument("--context", help="固有名詞・発言者候補を書いたテキストファイル（プロンプトに注入）")
    p.add_argument("--check-numbers", nargs=2, metavar=("TRANSCRIPT", "SUMMARY"),
                   help="要約から落ちた金額・台数と、原文に無い数値を検査するだけ（API 不要・無課金）。"
                        "Claude が作った要約の検証に使う")
    p.add_argument("--organize-only", metavar="TRANSCRIPT",
                   help="成果物の整理だけを行う（summary.md 以外を <stem>/ へ一括。API 不要・無課金）。"
                        "後処理を Claude 側で行ったあとに使う")
    p.add_argument("--gui", action="store_true", help="ファイル選択ダイアログを表示して実行")
    p.add_argument("--no-key-pool", action="store_true",
                   help="キーをプールしない（GEMINI_API_KEY_POOL を無視し、既定のキー1本だけを使う）")
    a = p.parse_args()

    # ── 数値チェックのみ（API を呼ばない＝無課金） ──────────────────────
    # 要約から金額・台数が落ちるのはプロンプトでは防ぎきれない（2026-08-04 実測で保持率 35%、
    # プロンプト改良後も 70%）。機械的な照合なのでモデルに任せず、後処理を誰が書いても
    # 同じ検査を通せるよう独立したモードにしてある。Claude が作った要約の検証に使う。
    if a.check_numbers:
        tpath, spath = (Path(x).expanduser() for x in a.check_numbers)
        src = tpath.read_text(encoding="utf-8")
        summary = spath.read_text(encoding="utf-8")
        miss = _missing_material_numbers(src, summary)
        fab = _fabricated_numbers(src, summary)
        print(f"原文: {tpath}\n要約: {spath}\n")
        if miss:
            print(f"⚠ 要約に含まれていない金額・台数が {len(miss)} 種:\n")
            for _, (raw, ctx) in sorted(miss.items()):
                print(f"  ● {raw}")
                print("\n".join("      " + ln for ln in ctx.splitlines()) + "\n")
        else:
            print("金額・台数の脱落なし")
        if fab:
            print(f"\n⚠ 原文に無い数値が要約にある（合計値などの可能性。原文を確認すること）: "
                  f"{', '.join(fab)}")
        sys.exit(1 if miss else 0)

    # ── 整理のみ（API を呼ばない＝無課金） ────────────────────────────
    # 後処理を Claude 側で行うと、従来 derive の直後に走っていた整理が実行されない。
    # 音声・チャンク・中間txt の拾い上げは手作業だと漏れるので単独モードにしてある。
    if a.organize_only:
        tpath = Path(a.organize_only).expanduser().resolve()
        if not tpath.exists():
            sys.exit(f"見つからない: {tpath}")
        stem = re.sub(r"_transcript$", "", tpath.stem)
        audio = next((p for p in tpath.parent.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
                      and p.stem.startswith(stem)), None)
        organize_outputs(tpath.parent, stem, audio=audio)
        sys.exit(0)

    if a.rpd:                                # RPD 目安を上書き
        FREE_TIER_RPD[a.model] = a.rpd

    global _API_KEY_DESC, _TIER_DECLARED
    # 実行の冒頭に出す。途中でエラー終了しても「どのモデル・どのキーで動いたか」が
    # 必ず残るようにするため（末尾のレポートだけだと失敗時に何も分からない）。
    print(f"── Gemini API ──\n  モデル: {a.model}（文字起こしのみ。後処理は Claude 側）")

    # キーの決め方は3通り。優先順は --api-key → GEMINI_API_KEY_POOL → 自動検出。
    # POOL を自動検出より優先するのは、複数キーがある環境で「どれを使うか」を
    # 環境変数名のアルファベット順という無関係な要因に委ねないため。
    if a.api_key:
        pool_entries, _TIER_DECLARED = [("コマンドライン --api-key", a.api_key)], "unknown"
    elif not a.no_key_pool and (declared := _pool_entries_from_declaration()):
        pool_entries, _TIER_DECLARED = declared, "free"
    else:
        key, key_origin = _resolve_api_key()
        if not key:
            sys.exit("エラー: Gemini API キーが未設定。GEMINI_API_KEY_POOL でプールを宣言するか、"
                     "環境変数 GEMINI_API_KEY を設定するか、"
                     "~/.config/claude-toolkit/gemini-api-key にキーを保存してください。"
                     "（取得: https://aistudio.google.com/apikey）")
        pool_entries = ([(key_origin, key)] if a.no_key_pool
                        else _discover_pool_entries(key, key_origin))
        _TIER_DECLARED = "unknown"
    if not pool_entries:
        sys.exit("エラー: GEMINI_API_KEY_POOL に有効なキーが1つもありません。")

    key_pool = KeyPool(pool_entries, declared_tier=_TIER_DECLARED)
    _API_KEY_DESC = f"{pool_entries[0][0]}（指紋 {_key_fingerprint(pool_entries[0][1])}）"
    # ラベル → 指紋。実行後のサマリでアカウント・プランを表示するために保持する（値は持たない）。
    _KEY_FINGERPRINTS.clear()
    _KEY_FINGERPRINTS.update({l: _key_fingerprint(k) for l, k in pool_entries})
    labels = "・".join(l for l, _ in pool_entries)
    if _TIER_DECLARED == "free":
        print(f"  キー: {len(pool_entries)}件をプール（{labels}）。枠上限で先頭から順に自動切替")
        print("    無料枠キーとして宣言されている（GEMINI_API_KEY_POOL）。課金状態は実測しない")
    elif len(pool_entries) > 1:
        print(f"  キー: {len(pool_entries)}件をプール（{labels}）。枠上限/枯渇で自動切替")
        print("    ⚠ 課金キーが混ざっていても検出できない。GEMINI_API_KEY_POOL での明示を推奨")
    else:
        print(f"  キー: {_API_KEY_DESC}")
    client = key_pool

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

    write_usage_report(out.parent, stem)
    # 整理はここでは行わない。summary がまだ無い段階（話者比定の作業用に transcript を
    # 直下に残す）なので、Step 4 まで終えてから --organize-only で実行する。
    print("\n生成ファイル:")
    print(f"  {out.parent / (stem + '_transcript.txt')}")
    print("\n次は話者比定（SKILL.md Step 3）→ ケバ取り・凝縮を Claude 側で生成（Step 4）。"
          "\nこのスクリプトが Gemini を呼ぶのは文字起こしだけ（後処理が課金の大半を占めていたため）。")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhaustedError as e:
        print(f"\n⛔ {e}", file=sys.stderr)
        print("  成功済みチャンクはキャッシュ済みです。解決後に同じコマンドを再実行すれば、"
              "未処理のチャンクだけが処理されます（再送信・二重課金は起きません）。", file=sys.stderr)
        sys.exit(2)
