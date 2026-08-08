#!/usr/bin/env python3
"""登録済みの Gemini API キーそれぞれの「今使えるか」を実際に API に問い合わせて表示する。

存在理由:
  Gemini API は残高・残枠を返さないため、429 が出たときに
  「無料枠の日次上限（待てば回復）」なのか「前払いクレジットの枯渇（購入が必要）」なのかを
  人が推測するしかなく、対処が正反対のため誤課金・時間の浪費が繰り返し起きた。
  だが 429 のエラーメッセージには理由が書かれている。実際に軽い呼び出しを投げて
  その分類を読めば、残高ページを見るまでもなく状態が確定する。それを機械化したもの。

  複数アカウントのキーを環境変数で持ち分ける構成では、「どのキーが実際に使われているか」の
  取り違えも起きる。キーは値を出さず指紋で識別し、由来（どの環境変数か・設定ファイルか）を併記する。

  ⚠ 課金枠か無料枠かの判定は行わない（2026-08-08 にプローブを廃止）。どのキーをプールに
  入れるかは GEMINI_API_KEY_POOL での宣言に委ね、課金状態は
  https://aistudio.google.com/billing で確認する。

使い方:
  python3 gemini-key-status.py           # 全キーの状態を表示
  python3 gemini-key-status.py --json    # JSON で出力
  python3 gemini-key-status.py --model gemini-3.5-flash-lite

終了コード: 0=実効キーが使える / 1=実効キーが使えない / 2=キーが1つも無い
"""

import argparse, hashlib, json, os, sys
from pathlib import Path

from google import genai
from google.genai import errors

CONFIG_FILE = Path.home() / ".config" / "claude-toolkit" / "gemini-api-key"
PING_MODEL = "gemini-3.5-flash-lite"     # 本スクリプト群が実際に使う唯一のモデルで状態を見る

# 課金枠か無料枠かの実測プローブ（無料枠を持たないモデルへ ping して 429 の種別を読む）は
# 2026-08-08 に廃止した。プールへ入れるキーを無料枠キーだけに限る運用へ変えたため、
# キーごとに課金状態を測る必要が無くなった（齋藤指示 2026-08-08）。
# 課金状態を確かめたいときは https://aistudio.google.com/billing を見る。


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def collect_keys() -> list:
    """(由来, キー) の一覧。実効キー（GEMINI_API_KEY）を先頭にする。"""
    found = []
    eff = os.environ.get("GEMINI_API_KEY")
    if eff:
        found.append(("環境変数 GEMINI_API_KEY（実効）", eff))
    for name, val in sorted(os.environ.items()):
        # GEMINI_API_KEY_POOL はキーではなくラベルの一覧（"NHO SAITOLA" 等）。
        # 接頭辞が一致するので、除外しないとラベル文字列をキーとして ping してしまう。
        if name.startswith("GEMINI_API_KEY") and name not in ("GEMINI_API_KEY", "GEMINI_API_KEY_POOL") and val:
            found.append((f"環境変数 {name}", val))
    if CONFIG_FILE.exists():
        val = CONFIG_FILE.read_text(encoding="utf-8").strip()
        if val:
            found.append((f"{CONFIG_FILE}", val))
    return found


def classify(e) -> tuple:
    """例外を (状態コード, 説明) に分類する。audio-transcribe.py の _quota_kind と同じ判定基準。"""
    code = getattr(e, "code", None)
    msg = str(getattr(e, "message", "") or e)
    low = msg.lower()
    if code == 429:
        if "prepayment" in low or "credits are depleted" in low or "credit balance" in low:
            return "CREDITS_DEPLETED", "前払いクレジット枯渇（購入するまで回復しない）"
        if "PerDay" in msg or "requests_per_day" in msg or "free_tier" in low:
            return "DAILY_LIMIT", "無料枠の日次上限（太平洋時間0時＝日本時間16時頃に回復）"
        if "PerMinute" in msg or "requests_per_minute" in msg:
            return "RATE_LIMIT", "分次レート制限（数十秒待てば回復）"
        return "QUOTA_OTHER", f"429（種別不明）: {msg[:120]}"
    if code in (401, 403):
        return "AUTH_ERROR", f"認証エラー（{code}）: キーが無効・失効・APIが未有効の可能性"
    if code == 404:
        return "MODEL_UNAVAILABLE", "モデルがこのキーで利用不可（404）"
    return "ERROR", f"{code}: {msg[:120]}"


def check(key: str, model: str) -> tuple:
    try:
        client = genai.Client(api_key=key)
        client.models.generate_content(model=model, contents="ping")
        return "OK", "使用可能"
    except (errors.ClientError, errors.ServerError) as e:
        return classify(e)
    except Exception as e:                       # ネットワーク等
        return "ERROR", str(e)[:120]


def main():
    p = argparse.ArgumentParser(description="Gemini API キーの利用可否を実測して表示する")
    p.add_argument("--model", default=PING_MODEL, help=f"ping に使うモデル（既定: {PING_MODEL}）")
    p.add_argument("--json", action="store_true", help="JSON で出力")
    a = p.parse_args()

    keys = collect_keys()
    if not keys:
        print("Gemini API キーが1つも見つかりません（環境変数 GEMINI_API_KEY / "
              f"{CONFIG_FILE}）", file=sys.stderr)
        sys.exit(2)

    # 同じキーを複数の由来が指していることがある（環境変数と設定ファイルの二重管理）。
    # 指紋でまとめて1回だけ問い合わせ、由来は列挙する（二重管理の可視化にもなる）。
    by_fp = {}
    for origin, key in keys:
        fp = fingerprint(key)
        entry = by_fp.setdefault(fp, {"fingerprint": fp, "origins": [], "key": key})
        entry["origins"].append(origin)

    results = []
    for fp, entry in by_fp.items():
        status, detail = check(entry["key"], a.model)
        results.append({"fingerprint": fp, "origins": entry["origins"],
                        "status": status, "detail": detail})

    effective = next((r for r in results
                      if any("実効" in o for o in r["origins"])), None)

    if a.json:
        print(json.dumps({"model": a.model, "keys": results,
                          "effective_ok": bool(effective and effective["status"] == "OK")},
                         ensure_ascii=False, indent=2))
    else:
        print(f"── Gemini API キーの状態（ping モデル: {a.model}）──")
        for r in results:
            mark = "✓" if r["status"] == "OK" else "✗"
            print(f"  {mark} 指紋 {r['fingerprint']}  {r['status']}")
            print(f"      {r['detail']}")
            for o in r["origins"]:
                print(f"      由来: {o}")
        if len(results) < len(keys):
            print("  ※ 同じキーが複数の由来で設定されています（上の「由来」欄が2行以上のもの）。"
                  "値がずれると原因不明の失敗になるため、正本を1つに決めることを推奨します。")
        if effective and effective["status"] == "CREDITS_DEPLETED":
            print("\n→ 実効キーはクレジット枯渇です。https://aistudio.google.com/billing で購入してください。")
            print("  待っても回復しません。無料枠キーが別にあるなら GEMINI_API_KEY を切り替える手もあります。")
        elif effective and effective["status"] == "DAILY_LIMIT":
            print("\n→ 実効キーは無料枠の日次上限です。日本時間16時頃に回復します（課金は不要）。")

    sys.exit(0 if (effective and effective["status"] == "OK") else 1)


if __name__ == "__main__":
    main()
