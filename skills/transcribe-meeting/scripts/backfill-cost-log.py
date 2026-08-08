#!/usr/bin/env python3
"""散在する <stem>_usage.json を累積コストログ（JSONL）へ取り込む。

`audio-transcribe.py` は 2026-08-05 から実行ごとに
`~/.config/claude-toolkit/gemini-cost-log.jsonl` へ追記するが、それ以前の実行は
成果物のフォルダに置かれた `<stem>_usage.json` にしか残っていない。本スクリプトは
それらを探して同じ形式で取り込み、過去の消費も `gemini-cost-report.py` で見られるようにする。

  backfill-cost-log.py --dry-run          # 何を取り込むか確認するだけ
  backfill-cost-log.py                    # 取り込む（既に入っている実行は飛ばす）
  backfill-cost-log.py --root ~/Projects  # 探索範囲を絞る（既定はホーム全体）

2点、値の性質が現行の記録と違う。
- **2026-08-04 より前の実行は thinking トークンが記録されていない**（記録の追加は commit
  4e77f4c）。実際には課金されていたので、それらの行は `thoughts_recorded: false` を立て、
  金額は下限値として扱う。レポート側も警告を出す。
- 古い `_usage.json` の `est_usd` は当時の誤った単価で書かれている。**現行の単価表（gemini_pricing.py）で
  引き直して**取り込む（当時の表示額は `est_usd_at_run` に残す）。
"""

import argparse, datetime as _dt, json, os, subprocess, sys
from pathlib import Path

# 単価表は同じディレクトリの gemini_pricing.py が正本（audio-transcribe.py も同じものを読む）。
# 本スクリプトは廃止済みモデルを含む過去の実行を引き直すため、**絞り込まない全モデルの表**を使う。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_pricing import JPY_PER_USD, rate

# 取り込み先はマシン別ファイル（audio-transcribe.py と同じレイアウト）。
# 過去分は「どのマシンで走ったか」が _usage.json に残っていないため、
# 取り込んだマシンのファイルではなく専用の backfill.jsonl に入れて由来を混ぜない。
COST_LOG_DIR = Path.home() / ".config" / "claude-toolkit" / "gemini-cost"
COST_LOG_PATH = COST_LOG_DIR / "backfill.jsonl"


def call_cost(c: dict) -> tuple:
    """1リクエストの (入力USD, 出力USD, thinkingUSD) を現行単価で計算する。"""
    pr = rate(c.get("model", ""), c.get("prompt", 0))
    if not pr:
        return 0.0, 0.0, 0.0
    audio = c.get("prompt_audio", 0)
    text = c.get("prompt_text", max(0, c.get("prompt", 0) - audio))
    c_in = (audio * pr["in_audio"] + text * pr["in"]) / 1e6
    c_out = c.get("candidates", 0) * pr["out"] / 1e6
    c_th = c.get("thoughts", 0) * pr["out"] / 1e6
    return c_in, c_out, c_th


def find_usage_files(root: Path) -> list:
    """<stem>_usage.json を探す。Library・node_modules は除く（時間の無駄と無関係な一致を避ける）。"""
    cmd = ["find", str(root), "-name", "*_usage.json",
           "-not", "-path", "*/node_modules/*", "-not", "-path", "*/Library/*"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [Path(p) for p in out.splitlines() if p.strip()]


def convert(path: Path) -> dict:
    """1つの _usage.json を累積ログの1行に変換する。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    calls = d.get("calls") or []
    stem = path.name[: -len("_usage.json")]
    mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime)

    c_in = c_out = c_th = 0.0
    for c in calls:
        a, b, e = call_cost(c)
        c_in += a
        c_out += b
        c_th += e
    usd = c_in + c_out + c_th
    q = d.get("quality") or {}
    stages = []
    for r in d.get("by_stage", []):
        sc = {"model": r.get("model"), "prompt": r.get("prompt", 0),
              "prompt_audio": r.get("prompt_audio", 0),
              "candidates": r.get("candidates", 0), "thoughts": r.get("thoughts", 0)}
        a, b, e = call_cost(sc)
        stages.append({"stage": r.get("stage"), "model": r.get("model"),
                       "calls": r.get("calls", 0), "tokens": r.get("total", 0),
                       "thoughts": r.get("thoughts", 0), "usd": round(a + b + e, 4)})

    # 最初期の _usage.json には calls 配列が無く by_stage だけがある。calls から出せない
    # ときは by_stage の合算で代替する（0円として取り込むと消費が過小に見える）。
    if not calls and stages:
        usd = sum(s["usd"] for s in stages)
        c_th = 0.0

    # thinking の記録は 2026-08-04 の commit 4e77f4c で追加された。それ以前の実行は
    # 実際には課金されていても記録が無いので、金額を下限値として扱う印を付ける。
    # 日付で切るのは、lite 単独の実行（本当に thinking が 0）を誤って下限値扱いしないため。
    THOUGHTS_LOGGED_SINCE = _dt.date(2026, 8, 4)
    recorded = mtime.date() >= THOUGHTS_LOGGED_SINCE or any(c.get("thoughts") for c in calls)
    return {
        "ts": mtime.strftime("%Y-%m-%d %H:%M:%S"),
        "pt_date": (mtime - _dt.timedelta(hours=16)).strftime("%Y-%m-%d"),  # JST→PT 近似
        "machine": d.get("machine") or "(backfill・不明)",
        "job": stem,
        "out_dir": str(path.parent),
        "billing_tier": d.get("billing_tier"),
        "billed": d.get("billed"),
        "api_key": d.get("api_key"),
        "usd": round(usd, 4),
        "jpy": round(usd * JPY_PER_USD),
        "thinking_usd": round(c_th, 4),
        "thinking_jpy": round(c_th * JPY_PER_USD),
        "thinking_pct": round(c_th / usd * 100) if usd else 0,
        "tokens": {
            "total": d.get("total_tokens", 0),
            "prompt": sum(c.get("prompt", 0) for c in calls),
            "prompt_audio": sum(c.get("prompt_audio", 0) for c in calls),
            "candidates": sum(c.get("candidates", 0) for c in calls),
            "thoughts": sum(c.get("thoughts", 0) for c in calls),
        },
        "requests": len(calls),
        "audio_min": q.get("covered_min"),
        "grade": q.get("grade"),
        "segments": q.get("segments"),
        "escalated": sum(1 for x in d.get("quality_detail", []) if x.get("escalated")),
        "retried": sum(1 for x in d.get("quality_detail", []) if (x.get("attempts") or 1) > 1),
        "flagged": q.get("flagged"),
        "elapsed_sec": d.get("elapsed_sec"),
        "api_sec": d.get("api_sec"),
        "thoughts_recorded": recorded,
        "stages": stages,
        "backfilled": True,
        "source": str(path),
        "est_usd_at_run": d.get("est_usd_approx"),   # 当時表示していた（誤った単価の）額
    }


def main() -> None:
    p = argparse.ArgumentParser(description="_usage.json を累積コストログへ取り込む")
    p.add_argument("--root", type=Path, default=Path.home(), help="探索の起点（既定はホーム）")
    p.add_argument("--log", type=Path, default=COST_LOG_PATH, help="書き込む累積ログ")
    p.add_argument("--dry-run", action="store_true", help="書き込まず一覧だけ出す")
    a = p.parse_args()

    existing = set()
    if a.log.exists():
        for line in a.log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            existing.add(r.get("source") or f"{r.get('ts')}|{r.get('job')}")

    files = find_usage_files(a.root)
    print(f"_usage.json を {len(files)} 件みつけた（探索: {a.root}）\n")
    new_rows, skipped = [], 0
    for f in sorted(files):
        try:
            row = convert(f)
        except (ValueError, OSError, KeyError) as e:
            print(f"  スキップ（読めない）: {f} — {e}")
            continue
        if str(f) in existing:
            skipped += 1
            continue
        new_rows.append(row)

    new_rows.sort(key=lambda r: r["ts"])
    for r in new_rows:
        mark = "" if r["thoughts_recorded"] else "  ⚠thinking未計上（下限値）"
        old = r.get("est_usd_at_run")
        delta = ""
        if old:
            delta = f"  当時の表示 {round(old * JPY_PER_USD):,}円 →"
        print(f"  {r['ts'][:10]}  {r['job'][:36]:<36}{delta} {r['jpy']:>6,}円"
              f"  思考{r['thinking_pct']:>3}%{mark}")

    print(f"\n新規 {len(new_rows)} 件 ／ 既に取り込み済み {skipped} 件")
    if not new_rows:
        return
    total = sum(r["jpy"] for r in new_rows)
    th = sum(r["thinking_jpy"] for r in new_rows)
    print(f"取り込む合計 {total:,} 円（うち thinking {th:,} 円）")
    if a.dry_run:
        print("\n--dry-run のため書き込んでいない。")
        return
    a.log.parent.mkdir(parents=True, exist_ok=True)
    with a.log.open("a", encoding="utf-8") as fh:
        for r in new_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n{a.log} へ追記した。閲覧: gemini-cost-report.py")


if __name__ == "__main__":
    main()
