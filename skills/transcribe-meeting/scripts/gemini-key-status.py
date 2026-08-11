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

  GEMINI_API_KEY_POOL が設定されている環境では、実行に使われるのはプールのキーであって
  実効キー（GEMINI_API_KEY）ではない。判定と案内はプールを基準にする。これを見ずに
  実効キーだけで結論すると、「プール外の課金キーが枯渇している」だけの状態を
  「文字起こしができない」と誤診する（2026-08-11 に実際に誤診した）。

終了コード:
  0 = 実行に使えるキーがある（プール設定時はプール内に1本以上 OK があること）
  1 = 使えるキーが無い
  2 = キーが1つも見つからない
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


def pool_labels() -> list:
    """GEMINI_API_KEY_POOL のラベル一覧（空白またはカンマ区切り）。未設定なら空。"""
    raw = os.environ.get("GEMINI_API_KEY_POOL", "").strip()
    return [t for t in raw.replace(",", " ").split() if t]


def pool_fingerprints() -> tuple:
    """(プール内キーの指紋集合, 環境変数が未設定だったラベル)。

    ラベル LABEL は環境変数 GEMINI_API_KEY_<LABEL> を指す（audio-transcribe.py と同じ規則）。
    """
    fps, missing = set(), []
    for label in pool_labels():
        val = os.environ.get(f"GEMINI_API_KEY_{label.upper()}", "").strip()
        if val:
            fps.add(fingerprint(val))
        else:
            missing.append(label)
    return fps, missing


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

    # プールが設定されていれば、実行に使われるのはプールのキー。判定の基準をそちらへ移す。
    pool_fps, pool_missing = pool_fingerprints()
    for r in results:
        r["in_pool"] = r["fingerprint"] in pool_fps
    pool_results = [r for r in results if r["in_pool"]]
    pool_ok = [r for r in pool_results if r["status"] == "OK"]
    usable = bool(pool_ok) if pool_fps else bool(effective and effective["status"] == "OK")

    if a.json:
        print(json.dumps({"model": a.model, "keys": results,
                          "pool": pool_labels(),
                          "pool_missing_env": pool_missing,
                          "pool_ok_count": len(pool_ok) if pool_fps else None,
                          "usable": usable,
                          "effective_ok": bool(effective and effective["status"] == "OK")},
                         ensure_ascii=False, indent=2))
    else:
        print(f"── Gemini API キーの状態（ping モデル: {a.model}）──")
        for r in results:
            mark = "✓" if r["status"] == "OK" else "✗"
            tag = "  [プール]" if r["in_pool"] else ""
            print(f"  {mark} 指紋 {r['fingerprint']}  {r['status']}{tag}")
            print(f"      {r['detail']}")
            for o in r["origins"]:
                print(f"      由来: {o}")
        if len(results) < len(keys):
            print("  ※ 同じキーが複数の由来で設定されています（上の「由来」欄が2行以上のもの）。"
                  "値がずれると原因不明の失敗になるため、正本を1つに決めることを推奨します。")
        if pool_missing:
            print(f"  ⚠ GEMINI_API_KEY_POOL のラベルに対応する環境変数が未設定: "
                  f"{'・'.join(pool_missing)}（GEMINI_API_KEY_<ラベル> を設定する）")

        if pool_fps:
            # プール運用時は実効キーの状態は結論に関係しない（プール外なら使われない）。
            print(f"\n→ プール（GEMINI_API_KEY_POOL=\"{' '.join(pool_labels())}\"）"
                  f"のうち {len(pool_ok)}/{len(pool_results)} 本が使用可能。")
            if pool_ok:
                print("  文字起こしはこのプールのキーだけを使うため、実行できます。")
            else:
                print("  プールのキーが全滅しています。日次上限なら日本時間16時頃に回復します。")
            if effective and effective["status"] != "OK" and not effective["in_pool"]:
                print(f"  ※ 実効キー（GEMINI_API_KEY）は {effective['status']} ですが"
                      "プール外なので、プールを使う処理には影響しません。")
                if effective["status"] == "CREDITS_DEPLETED":
                    print("     このキーを直接使う用途（機密案件など）だけが購入を要します"
                          "→ https://aistudio.google.com/billing")
        elif effective and effective["status"] == "CREDITS_DEPLETED":
            print("\n→ 実効キーはクレジット枯渇です。https://aistudio.google.com/billing で購入してください。")
            print("  待っても回復しません。無料枠キーが別にあるなら GEMINI_API_KEY を切り替える手もあります。")
        elif effective and effective["status"] == "DAILY_LIMIT":
            print("\n→ 実効キーは無料枠の日次上限です。日本時間16時頃に回復します（課金は不要）。")

    sys.exit(0 if usable else 1)


if __name__ == "__main__":
    main()
