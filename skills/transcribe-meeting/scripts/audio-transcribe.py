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

import argparse, datetime as _dt, json, os, re, shutil, subprocess, sys, time
from collections import Counter
from pathlib import Path

from google import genai
from google.genai import errors, types


# ── モデル・閾値 ────────────────────────────────────────────────
TRANSCRIBE_MODEL = "gemini-2.5-flash"   # 文字起こし既定（高速・低コスト優先）
PRO_MODEL        = "gemini-2.5-pro"     # 品質不良時の自動格上げ先
FAST_MODEL       = "gemini-2.5-flash"   # 課金枠制限時のフォールバック先
POST_MODEL       = "gemini-2.5-flash"   # verbatim / summary 生成（後処理）
LONG_AUDIO_THRESHOLD_SEC = 15 * 60      # これを超える音声は分割モードへ直行
DEFAULT_CHUNK_MIN = 15                  # 分割時のチャンク長（分）。大きいほど総リクエスト数が減る
MIN_CHARS_PER_SEC = 1.5                 # チャンク文字数の下限目安（下回れば途切れの疑い）
POST_BLOCK_CHARS = 24000                # verbatim/summary を分割処理する塊サイズ

# 概算単価（USD / 100 万トークン）。正確な請求額ではない。必要に応じ更新。
PRICING = {
    "gemini-2.5-pro":   {"in": 1.25, "out": 10.0},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
}

# 無料枠の1日リクエスト数（RPD）の目安。Google が随時改定するため正本ではない
# （2026年に 20→250→1,500 と変動報告あり）。正確な残枠は Google AI Studio のダッシュボードで確認する。
# 環境変数 GEMINI_FLASH_RPD / GEMINI_PRO_RPD、または --rpd で上書き可。
FREE_TIER_RPD = {
    "gemini-2.5-flash": int(os.environ.get("GEMINI_FLASH_RPD", "250")),
    "gemini-2.5-pro":   int(os.environ.get("GEMINI_PRO_RPD", "100")),
}
# 当ツール経由のリクエスト数・トークン数を太平洋時間の日付ごとに積算する（無料枠消費の目安）
DAILY_TALLY_PATH = Path.home() / ".config" / "claude-toolkit" / "gemini-usage.json"

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
def _record_usage(stage: str, model: str, resp, sec: float = 0.0) -> None:
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return
    USAGE_LOG.append({
        "stage": stage, "model": model, "sec": round(sec, 2),
        "prompt": getattr(um, "prompt_token_count", 0) or 0,
        "candidates": getattr(um, "candidates_token_count", 0) or 0,
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
    """当ツールの1リクエストを PT 日付・モデル別に積算する（他アプリの消費は含まない）。"""
    try:
        DAILY_TALLY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if DAILY_TALLY_PATH.exists():
            data = json.loads(DAILY_TALLY_PATH.read_text(encoding="utf-8"))
        m = data.setdefault(_pt_date(), {}).setdefault(model, {"requests": 0, "tokens": 0})
        m["requests"] += 1
        m["tokens"] += int(tokens or 0)
        for old in sorted(data)[:-14]:       # 直近14日ぶんだけ保持
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
    print(f"\n── 無料枠の本日消費（ローカル集計・太平洋時間 {day} 基準） ──")
    for model, m in sorted(today.items()):
        rpd = FREE_TIER_RPD.get(model)
        req = m["requests"]
        if rpd:
            print(f"  {model:<20}本日 {req} リクエスト / 目安 {rpd}（残り約 {max(0, rpd - req)}）"
                  f"  累計トークン {m['tokens']:,}")
        else:
            print(f"  {model:<20}本日 {req} リクエスト  累計トークン {m['tokens']:,}")
    print("  ※ 当ツール経由の集計。RPD目安はGoogleが随時改定するため、正確な残枠は Google AI Studio で確認。")


class QuotaExhaustedError(RuntimeError):
    """無料枠の1日リクエスト上限に達した（当日中は再実行しても回復しない）。上位で明確に案内して停止する。"""


def _is_tier_block(e) -> bool:
    """このモデルがAPIキーの枠で使えない（limit: 0）か。真なら呼び出し側で別モデルへ格下げすべき。"""
    if getattr(e, "code", None) != 429:
        return False
    return "limit: 0" in str(getattr(e, "message", "") or e)


def _quota_kind(e) -> str:
    """429 のクォータ種別を返す。'day'（日次上限＝当日ハードストップ）／'minute'（分次＝待てば回復）／''。"""
    if getattr(e, "code", None) != 429:
        return ""
    msg = str(getattr(e, "message", "") or e)
    if "PerDay" in msg or "requests_per_day" in msg:
        return "day"
    if "PerMinute" in msg or "requests_per_minute" in msg:
        return "minute"
    if "free_tier" in msg or "FreeTier" in msg:   # 期間表記が無い free tier は保守的に日次扱い
        return "day"
    return ""


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
            if code == 429 and _quota_kind(e) == "day":
                raise QuotaExhaustedError(
                    "Gemini API 無料枠の1日リクエスト上限に達しました。"
                    "当日中は再実行しても回復しません（リセットは太平洋時間0時＝日本時間16時頃）。"
                    "billing を有効化するか、枠リセット後に再実行してください。")
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


def write_usage_report(out_dir: Path, stem: str):
    """stage 別・モデル別のトークン消費・所要時間・品質を表示し、_usage.json に保存する。"""
    if not USAGE_LOG and not QUALITY_LOG:
        return None
    by_key: dict = {}
    for u in USAGE_LOG:
        d = by_key.setdefault((u["stage"], u["model"]),
                              {"calls": 0, "prompt": 0, "candidates": 0, "total": 0, "sec": 0.0})
        d["calls"] += 1
        for f in ("prompt", "candidates", "total"):
            d[f] += u[f]
        d["sec"] += u.get("sec", 0.0)
    total_tokens = sum(u["total"] for u in USAGE_LOG)
    api_sec = sum(u.get("sec", 0.0) for u in USAGE_LOG)
    elapsed = time.time() - _START
    rows, est_cost = [], 0.0
    for (stage, model), d in sorted(by_key.items()):
        pr = PRICING.get(model, {"in": 0.0, "out": 0.0})
        cost = d["prompt"] / 1e6 * pr["in"] + d["candidates"] / 1e6 * pr["out"]
        est_cost += cost
        rows.append({"stage": stage, "model": model, **d,
                     "sec": round(d["sec"], 1), "est_usd": round(cost, 4)})
    quality = _quality_summary()
    report = {"total_tokens": total_tokens, "est_usd_approx": round(est_cost, 4),
              "elapsed_sec": round(elapsed, 1), "elapsed_human": _fmt_dur(elapsed),
              "api_sec": round(api_sec, 1),
              "note": "est_usd はテキスト単価による概算。音声入力の実請求とは異なる。"
                      "elapsed_sec は本コマンドの総経過時間、api_sec は Gemini 応答待ちの合計。",
              "quality": quality, "by_stage": rows,
              "quality_detail": QUALITY_LOG, "calls": USAGE_LOG}
    path = out_dir / f"{stem}_usage.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n── トークン消費（Gemini API） ──")
    for r in rows:
        print(f"  {r['stage']:<14}{r['model']:<20}calls={r['calls']:>2}  "
              f"total={r['total']:>9,}  (in={r['prompt']:,} / out={r['candidates']:,})  "
              f"{r['sec']:>6.1f}秒  ~${r['est_usd']}")
    print(f"  合計 {total_tokens:,} トークン ／ 概算 ${round(est_cost, 4)}")
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


def check_quality(text: str, min_chars: int = None) -> tuple:
    """文字化け・ループ・途切れを検出。(ok, reason) を返す。"""
    t = (text or "").strip()
    if not t:
        return False, "空の出力"
    m = re.search(r'(.)\1{9,}', t)                       # 同一文字の連続
    if m:
        return False, f"同一文字の連続を検出: {m.group(0)[:20]!r}"
    reason = _detect_loop(t)                             # 反復ループ
    if reason:
        return False, reason
    if min_chars and len(re.sub(r'\s', '', t)) < min_chars:   # 尺に対して短すぎ＝途切れ
        return False, f"文字数が想定を大きく下回る（{len(t)}字 / 目安{min_chars}字）＝途切れの疑い"
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
    with open(path, "rb") as fh:
        up = client.files.upload(
            file=fh, config=types.UploadFileConfig(mime_type=mime, display_name=path.name))
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


def escalate(model: str) -> str:
    """pro でなければ pro へ格上げ。既に pro ならそのまま。"""
    return PRO_MODEL if model != PRO_MODEL else model


def transcribe_with_recovery(client, path: Path, prompt: str, model: str,
                             stage: str = "transcribe", duration_sec: float = None,
                             depth: int = 0) -> str:
    """1チャンクを文字起こしし、品質不良なら再試行→格上げ→再分割で回復する。"""
    min_chars = int(duration_sec * MIN_CHARS_PER_SEC) if duration_sec else None
    attempts = []
    for attempt in range(2):                       # 初回＋再試行1
        m = model if attempt == 0 else escalate(model)
        text = collapse_loops(transcribe_file(client, path, prompt, m, stage))
        ok, reason = check_quality(text, min_chars=min_chars)
        if ok:
            _record_quality(path.name, duration_sec, text, True, "",
                            attempt + 1, escalated=(m != model))
            return text
        print(f"    ⚠ 品質不良: {reason} → 再試行（{attempt + 1}/2）")
        attempts.append((text, reason))
    # 再分割（5分より長く、深さ上限未満なら半分に割って個別処理。子区間が各自品質を記録）
    if duration_sec and duration_sec > 300 and depth < 2:
        print(f"    ↳ {path.name} をさらに分割して再処理")
        subs = split_audio(path, max(150, int(duration_sec / 2)), tag=f"sub{depth}")
        return "\n".join(
            transcribe_with_recovery(client, c, prompt, escalate(model), stage,
                                     probe_duration(c), depth + 1)
            for c in subs)
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
        cache = _chunk_cache_path(chunk)
        if cache.exists() and cache.stat().st_size > 0:
            cached = cache.read_text(encoding="utf-8")
            ok, _ = check_quality(cached, min_chars=min_chars)
            if ok:                                    # 品質を満たす済チャンクのみ再利用
                print(f"[{i + 1}/{n}] キャッシュ再利用: {cache.name}")
                parts.append(f"## Part {i + 1} — {chunk.name}\n\n{cached}")
                continue
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


SUMMARY_LEGEND = ("> 話者比定の確度：無印＝確実／○＝推定（高）／△＝推定（低）／？＝不明。"
                  "自動文字起こしからの校正物であり確定議事録ではない。\n\n")


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
    """トランスクリプトから verbatim（ケバ取り）と summary（凝縮）を生成する。"""
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
    summary_out.write_text(SUMMARY_LEGEND + summary + "\n", encoding="utf-8")
    print(f" 完了: {summary_out.name}")
    return verbatim_out, summary_out


# ── API キー・GUI ───────────────────────────────────────────────
def _load_api_key_from_config() -> str:
    config_file = Path.home() / ".config" / "claude-toolkit" / "gemini-api-key"
    if config_file.exists():
        return config_file.read_text(encoding="utf-8").strip()
    return ""


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
    p.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY") or _load_api_key_from_config())
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
    p.add_argument("--no-organize", action="store_true",
                   help="成果物の整理をしない（既定は summary.md 以外を <stem>/ に一括）")
    a = p.parse_args()
    if a.rpd:                                # RPD 目安を上書き（対象モデル＋既定 flash）
        FREE_TIER_RPD[a.model] = a.rpd
        FREE_TIER_RPD["gemini-2.5-flash"] = a.rpd

    if not a.api_key:
        sys.exit("エラー: Gemini API キーが未設定。環境変数 GEMINI_API_KEY を設定するか、"
                 "~/.config/claude-toolkit/gemini-api-key にキーを保存してください。"
                 "（取得: https://aistudio.google.com/apikey）")
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
        v, s = derive_files(client, transcript, tpath.parent, stem)
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
            ok, why = check_quality(text, min_chars=min_chars)
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
        derive_files(client, result, out.parent, stem)

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
        print(f"  {out}")
        if not a.no_derive:
            print(f"  {out.parent / (stem + '_verbatim.txt')}")
            print(f"  {out.parent / (stem + '_summary.md')}")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhaustedError as e:
        print(f"\n⛔ {e}", file=sys.stderr)
        sys.exit(2)
