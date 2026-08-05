#!/usr/bin/env python3
"""既存のコストログから案件名を取り除き、実名を端末ローカルの対応表へ移す。

コストログ（`~/.config/claude-toolkit/gemini-cost/*.jsonl`）は複数マシンで共有するため
git 管理下に置くことがある。そこに `job`（＝音声ファイル名由来の案件名）を書いていると、
会議名・人名がそのまま履歴に残る。健康情報や人事情報に触れる会議名のようにコミットできない
ものが混ざるため、構造的に避ける必要がある。

- `job` → `job_id`（sha256 の先頭8桁。同じ案件名なら常に同じ id）
- `out_dir` / `source`（成果物の絶対パス。案件名を含む）→ 削除
- 実名は `job-names.local.json` に退避（`.local.json` は gitignore 対象にする運用）

冪等。すでに `job_id` 化されている行は触らない。`--dry-run` で変更内容だけ表示する。
実名は対応表に残るので、匿名化後も `gemini-cost-report.py` は案件名で絞り込める。

**注意：git 履歴に既にコミットされた実名はこの操作では消えない。** 過去のコミットから
消すには履歴の書き換え（force push）が必要で、それは利用者の判断事項。
"""
import argparse, hashlib, json, sys
from pathlib import Path

LOG_DIR = Path.home() / ".config" / "claude-toolkit" / "gemini-cost"
MAP_PATH = LOG_DIR / "job-names.local.json"
DROP = ("out_dir", "source")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(LOG_DIR), help=f"コストログのディレクトリ（既定: {LOG_DIR}）")
    ap.add_argument("--dry-run", action="store_true", help="書き換えずに変更内容を表示する")
    a = ap.parse_args()

    log_dir = Path(a.dir).expanduser()
    files = sorted(log_dir.glob("*.jsonl"))
    if not files:
        print(f"jsonl が見つかりません: {log_dir}", file=sys.stderr)
        return 1

    mapping = {}
    if MAP_PATH.exists():
        try:
            mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        except ValueError:
            mapping = {}

    total_changed = 0
    for f in files:
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        out, changed = [], 0
        for l in lines:
            r = json.loads(l)
            job = r.pop("job", None)
            dropped = {k: r.pop(k) for k in DROP if k in r}
            if job is None and not dropped:
                out.append(json.dumps(r, ensure_ascii=False))
                continue
            if job is not None:
                jid = hashlib.sha256(job.encode("utf-8")).hexdigest()[:8]
                entry = mapping.setdefault(jid, {})
                entry["job"] = job
                for k, v in dropped.items():
                    entry.setdefault(k, v)
                # job_id をキー順の先頭付近に置きたいので作り直す
                r = {**{k: r[k] for k in ("ts", "pt_date", "machine") if k in r},
                     "job_id": jid,
                     **{k: v for k, v in r.items() if k not in ("ts", "pt_date", "machine")}}
            elif r.get("job_id"):
                entry = mapping.setdefault(r["job_id"], {})
                for k, v in dropped.items():
                    entry.setdefault(k, v)
            changed += 1
            out.append(json.dumps(r, ensure_ascii=False))
        if changed:
            total_changed += changed
            print(f"{f.name}: {changed}/{len(lines)} 行を匿名化"
                  + ("（dry-run・未書き込み）" if a.dry_run else ""))
            if not a.dry_run:
                f.write_text("\n".join(out) + "\n", encoding="utf-8")
        else:
            print(f"{f.name}: 変更なし（すでに匿名化済み）")

    if total_changed and not a.dry_run:
        MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n実名の対応表: {MAP_PATH}（{len(mapping)} 件）")
        print("このファイルは gitignore 対象にすること。git 履歴に既にある実名は本操作では消えない。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
