#!/usr/bin/env python3
"""transcribe-meeting の Gemini 課金を累積ログから集計して表示する。

`audio-transcribe.py` が実行ごとに1行追記する
`~/.config/claude-toolkit/gemini-cost/<マシン名>.jsonl` を**すべて読んで合算**し、
案件別・工程別・日別・マシン別に「どこにいくら使ったか」を出す。引数なしで全体像が出る。

  gemini-cost-report.py                  # 累積・工程別・案件別・日別・マシン別
  gemini-cost-report.py --days 7         # 直近7日ぶんに絞る
  gemini-cost-report.py --job kobayashi  # 案件名の部分一致で絞る
  gemini-cost-report.py --calls          # 案件を1件ずつ工程内訳つきで出す
  gemini-cost-report.py --json           # 集計結果を JSON で出す（他ツールへの受け渡し用）

ログはマシンごとに別ファイル。ディレクトリを git 等で同期しておけば全マシンの消費が
合算して出る（同期の配線は各自の環境側で行う。本スクリプトは同期方法を知らない）。
同期していないマシンの消費は入らないので、**残高・無料枠の判定には使わない**
（判定は gemini-key-status.py の実測を使う）。
"""

import argparse, datetime as _dt, json, sys
from pathlib import Path

COST_LOG_DIR = Path.home() / ".config" / "claude-toolkit" / "gemini-cost"
LEGACY_LOG = Path.home() / ".config" / "claude-toolkit" / "gemini-cost-log.jsonl"
JPY_PER_USD = 155


def load(log_dir: Path) -> tuple:
    """ディレクトリ内の全 *.jsonl を読んで1つのリストにする。

    壊れた行は捨てて残りを活かす（追記専用ログなので書き込み途中の断裂があり得る)。
    旧レイアウトの単一ファイルも読む（ディレクトリ方式へ移す前の記録を失わないため）。"""
    files = sorted(log_dir.glob("*.jsonl")) if log_dir.is_dir() else []
    if LEGACY_LOG.exists():
        files.append(LEGACY_LOG)
    if not files:
        sys.exit(f"累積ログがない: {log_dir}/*.jsonl\n"
                 "文字起こしを1回実行すると作られる。過去分の取り込みは backfill-cost-log.py。")
    rows = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r.setdefault("machine", f.stem)
            rows.append(r)
    return rows, files


def jpy(row: dict) -> int:
    """円換算。記録済みの jpy を優先し、無ければ usd から換算する。"""
    v = row.get("jpy")
    return int(v) if v is not None else round((row.get("usd") or 0) * JPY_PER_USD)


def bar(value: float, total: float, width: int = 24) -> str:
    """構成比を見るための横棒。金額の桁を追わずに主因が分かるようにする。"""
    n = int(round(value / total * width)) if total else 0
    return "█" * n + "·" * (width - n)


def main() -> None:
    p = argparse.ArgumentParser(description="Gemini 文字起こしの課金を累積ログから集計する")
    p.add_argument("--days", type=int, help="直近 N 日ぶんに絞る")
    p.add_argument("--job", help="案件名（job）の部分一致で絞る")
    p.add_argument("--calls", action="store_true", help="案件ごとに工程の内訳を出す")
    p.add_argument("--json", action="store_true", dest="as_json", help="集計結果を JSON で出す")
    p.add_argument("--log-dir", type=Path, default=COST_LOG_DIR, help="読むログのディレクトリ")
    a = p.parse_args()

    rows, files = load(a.log_dir)
    if a.days:
        since = (_dt.date.today() - _dt.timedelta(days=a.days)).isoformat()
        rows = [r for r in rows if (r.get("ts") or "")[:10] >= since]
    if a.job:
        rows = [r for r in rows if a.job.lower() in (r.get("job") or "").lower()]
    if not rows:
        sys.exit("条件に合う記録がない。")

    total_jpy = sum(jpy(r) for r in rows)
    th_jpy = sum(int(r.get("thinking_jpy") or 0) for r in rows)
    # billed が True の行だけが実際に請求された分。無料枠キーの実行を混ぜると額を誤る
    billed_jpy = sum(jpy(r) for r in rows if r.get("billed") is True)
    free_jpy = sum(jpy(r) for r in rows if r.get("billed") is False)
    unknown_jpy = total_jpy - billed_jpy - free_jpy
    partial = [r for r in rows if not r.get("thoughts_recorded", True)]
    audio_min = sum(float(r.get("audio_min") or 0) for r in rows)
    reqs = sum(int(r.get("requests") or 0) for r in rows)

    stage_agg: dict = {}
    for r in rows:
        for s in r.get("stages", []):
            k = (s.get("stage"), s.get("model"))
            d = stage_agg.setdefault(k, {"calls": 0, "jpy": 0.0, "thoughts": 0})
            d["calls"] += int(s.get("calls") or 0)
            d["jpy"] += (s.get("usd") or 0) * JPY_PER_USD
            d["thoughts"] += int(s.get("thoughts") or 0)

    day_agg: dict = {}
    host_agg: dict = {}
    for r in rows:
        d = day_agg.setdefault((r.get("ts") or "")[:10], {"jpy": 0, "think": 0, "jobs": 0})
        d["jpy"] += jpy(r)
        d["think"] += int(r.get("thinking_jpy") or 0)
        d["jobs"] += 1
        h = host_agg.setdefault(r.get("machine") or "(不明)", {"jpy": 0, "jobs": 0, "think": 0})
        h["jpy"] += jpy(r)
        h["jobs"] += 1
        h["think"] += int(r.get("thinking_jpy") or 0)

    if a.as_json:
        print(json.dumps({
            "total_jpy": total_jpy, "thinking_jpy": th_jpy,
            "billed_jpy": billed_jpy, "free_jpy": free_jpy, "unknown_jpy": unknown_jpy,
            "runs": len(rows), "requests": reqs, "audio_min": round(audio_min, 1),
            "by_stage": [{"stage": k[0], "model": k[1], **v} for k, v in stage_agg.items()],
            "by_day": [{"date": k, **v} for k, v in sorted(day_agg.items())],
            "by_machine": [{"machine": k, **v} for k, v in sorted(host_agg.items())],
            "sources": [str(f) for f in files],
            "rows": rows,
        }, ensure_ascii=False, indent=2))
        return

    span = f"{min((r.get('ts') or '')[:10] for r in rows)} 〜 {max((r.get('ts') or '')[:10] for r in rows)}"
    hosts = "・".join(sorted(host_agg))
    print(f"\n=== Gemini 文字起こしコスト（{span}） ===")
    print(f"  マシン: {hosts}（{len(files)} ファイル合算）")
    print(f"  実行 {len(rows)} 回 ／ API リクエスト {reqs:,} 回 ／ 音声 {audio_min:,.0f} 分")
    print(f"  合計 {total_jpy:,} 円"
          + (f"（うち実請求 {billed_jpy:,} 円 ／ 無料枠 {free_jpy:,} 円"
             + (f" ／ 判定不能 {unknown_jpy:,} 円" if unknown_jpy else "") + "）"
             if (free_jpy or unknown_jpy) else ""))
    if th_jpy:
        print(f"  うち thinking（思考トークン）{th_jpy:,} 円"
              f"（{round(th_jpy / total_jpy * 100) if total_jpy else 0}%）"
              "  ← 出力単価で課金される。lite 系は発生しない")
    if audio_min:
        print(f"  音声1時間あたり 約 {round(total_jpy / audio_min * 60):,} 円")
    if partial:
        print(f"  ⚠ {len(partial)} 件は 2026-08-04 以前の記録で thinking が未計上。実額はこれより大きい（下限値）")

    print("\n── 工程・モデル別（高い順） ──")
    for (stage, model), d in sorted(stage_agg.items(), key=lambda x: -x[1]["jpy"]):
        print(f"  {bar(d['jpy'], total_jpy)} {d['jpy']:>7,.0f}円  "
              f"{stage:<15}{model:<24}calls={d['calls']:>3}"
              + (f"  思考{d['thoughts']:,}tok" if d["thoughts"] else ""))

    print("\n── 案件別（高い順） ──")
    for r in sorted(rows, key=jpy, reverse=True):
        flag = ""
        if r.get("escalated"):
            flag += f" 格上げ{r['escalated']}"
        if r.get("flagged"):
            flag += f" 要確認{r['flagged']}"
        if not r.get("thoughts_recorded", True):
            flag += " thinking未計上"
        am = f"{float(r['audio_min']):.0f}分" if r.get("audio_min") else "—"
        print(f"  {bar(jpy(r), total_jpy)} {jpy(r):>7,}円  {(r.get('ts') or '')[:10]}  "
              f"{(r.get('job') or '?')[:32]:<32} {am:>6}  req={r.get('requests') or 0:>3}"
              f"  思考{r.get('thinking_pct') or 0:>3}%{flag}")

    if len(host_agg) > 1:
        print("\n── マシン別 ──")
        for h, d in sorted(host_agg.items(), key=lambda x: -x[1]["jpy"]):
            print(f"  {bar(d['jpy'], total_jpy)} {d['jpy']:>7,}円  {h:<20}実行{d['jobs']:>3}件"
                  f"  （思考 {d['think']:,}円）")

    print("\n── 日別 ──")
    for day, d in sorted(day_agg.items()):
        print(f"  {day}  {d['jpy']:>7,}円  （思考 {d['think']:>6,}円）  実行{d['jobs']}件")

    if a.calls:
        print("\n── 案件ごとの工程内訳 ──")
        for r in sorted(rows, key=lambda x: (x.get("ts") or "")):
            print(f"\n  {(r.get('ts') or '')} {r.get('job')}  計 {jpy(r):,}円"
                  f"  [{r.get('grade') or '品質記録なし'}]  {r.get('machine') or ''}")
            for s in sorted(r.get("stages", []), key=lambda x: -(x.get("usd") or 0)):
                print(f"     {round((s.get('usd') or 0) * JPY_PER_USD):>6,}円  "
                      f"{s.get('stage'):<15}{s.get('model'):<24}"
                      f"calls={s.get('calls'):>3}  tok={s.get('tokens') or 0:>8,}"
                      + (f"  思考{s['thoughts']:,}" if s.get("thoughts") else ""))

    print(f"\nログ: {a.log_dir}/*.jsonl")
    print("※ 同期していないマシンの消費は入らない。残高・無料枠の判定には使わない"
          "（gemini-key-status.py で実測する）。")


if __name__ == "__main__":
    main()
