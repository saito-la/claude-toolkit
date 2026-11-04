#!/usr/bin/env python3
"""pj-learn.py — 「この文でこのプロジェクトを選んだ」を憶え、次の類似案件で当てる。

pj-ngram.py が照合するテキストへ、過去に選ばれたときのクエリ文を足し込むためのストア。
キーワードを抽出も生成もしない。文をそのまま憶えるだけで、2文字ずつ刻む照合（N-gram）が
語形の揺れを吸収する。「安全性報告の様式」と「SAE報告のフォーマット」は `報告` を共有する。
言い当てられる語を人が選ぶ必要が無いのが、この方式を採った理由（2026-08-29）。

記録するのは L1（決定的語彙一致）が外れたときだけにする。DIRECT で開けた文は既に
aliases か path が当てており、憶えても順位が変わらないうえ、よく使うプロジェクトの
テキストだけが太って何にでも当たるようになる。

置き場は既定で ~/.local/state/pj/（環境変数 PJ_STATE_DIR で変えられる）。~/.cache/pj/ ではないのは、索引と違って再生成できないため
（索引は CLAUDE.md から何度でも作れるが、これは選択の履歴そのもの）。リポジトリの中にも
置かない。pj は worktree の中で起動することがあり、追跡外ファイルは worktree へ引き継が
れないので、置くと学習が worktree ごとに分断される。

クエリ文には人名・案件名がそのまま残る。git には入らずローカルに留まるが、機密区分の
扱いとしては会話ログ（~/.claude/projects/）と同じ平文ローカルデータとして見る。

Usage:
  pj-learn.py record "<クエリ原文>" <relpath>   選択を記録
  pj-learn.py forget "<クエリ原文>" <relpath>   誤記録を取り消す
  pj-learn.py list [<relpath>]                  記録を一覧
"""
import json
import os
import platform
import pathlib
import subprocess
import sys
import time

# Windows では標準出力が cp932・CRLF になり、呼び出す bash が日本語と行末を読み違える。
# UTF-8・LF に揃える（pj-platform.sh の冒頭）
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", newline="\n")

# 保存先。複数の端末で学習を共有したいときは、git の外で同期される置き場を環境変数
# PJ_STATE_DIR に指す（指した先の親フォルダが無ければ既定へ戻る）。既定は ~/.local/state/pj。
#
# **git には載せない。** クエリ文に人名・案件名が残るため。
_LOCAL = pathlib.Path.home() / ".local" / "state" / "pj"
_ENV = os.environ.get("PJ_STATE_DIR", "").strip()
STATE_DIR = pathlib.Path(os.path.expanduser(_ENV)) if _ENV and pathlib.Path(os.path.expanduser(_ENV)).parent.is_dir() else _LOCAL

# **機体ごとにファイルを分ける。** 各機は自分のファイルだけを書くので、unison の
# 「両側で変更」が原理的に起きない。1ファイルを共有すると、_write() が毎回全体を
# 書き直す作りのため必ず衝突し、batch=true のプロファイルでは skipped が静かに
# 積み上がって同期されないまま気づかない状態になる。
# 端末名は LocalHostName を小文字にしたもの。nodename は接続先の逆引き DNS で変わり、
# 別の機体として新しいファイルを作ってしまうため使わない。
# Windows には scutil も os.uname も無いので COMPUTERNAME を使う。
def _machine_name():
    if os.name == "nt":
        return (os.environ.get("COMPUTERNAME") or platform.node() or "windows").lower()
    try:
        n = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
    except OSError:
        n = ""
    return (n or os.uname().nodename.split(".")[0]).lower()


HOST = _machine_name()
STORE = STATE_DIR / f"learned.{HOST}.jsonl"
LEGACY_STORE = STATE_DIR / "learned.jsonl"

# 1プロジェクトあたりに憶えるクエリ文の上限。増やすほど当たりやすくなる一方、
# よく使うプロジェクトのテキストが太って何にでも当たるようになる。古い順に捨てる。
PER_PATH_LIMIT = 10

# 短すぎる文は憶えない。プロジェクト名そのものは L1 が拾うし、N-gram では
# 2〜3文字が多数のプロジェクトに当たってノイズにしかならない。
MIN_QUERY_LEN = 4


def _read_file(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("q") and rec.get("path"):
            out.append(rec)
    return out


def _migrate():
    """機体別へ分ける前の learned.jsonl があれば自分のファイルとして引き継ぐ。"""
    if LEGACY_STORE.exists() and not STORE.exists():
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(LEGACY_STORE, STORE)


def _store_files():
    if not STATE_DIR.is_dir():
        return []
    return sorted(STATE_DIR.glob("learned.*.jsonl"))


def _read_own():
    """自分の機体のファイルだけ。書き戻す前の読み出しに使う。"""
    _migrate()
    return _read_file(STORE)


def _read():
    """全機体のファイルを併合して返す。

    (q, path) で重複排除し、ts の新しいものを採る。順序に依存しないので、どの機から
    見ても同じ結果になる。
    """
    _migrate()
    best = {}
    for f in _store_files():
        for rec in _read_file(f):
            key = (rec["q"], rec["path"])
            cur = best.get(key)
            if cur is None or rec.get("ts", 0) > cur.get("ts", 0):
                best[key] = rec
    return sorted(best.values(), key=lambda r: r.get("ts", 0))


def _write(records):
    """path ごとに直近 PER_PATH_LIMIT 件へ間引いてから書き戻す。

    追記だけにするとファイルが際限なく伸び、読み込みのたびに間引く処理が要る。
    記録は多くても数百行なので、書くたびに整えるほうが状態が1つで済む。
    """
    keep, seen = [], {}
    for rec in reversed(records):                     # 新しい順に見て上限まで採る
        key = rec["path"]
        seen.setdefault(key, 0)
        if seen[key] >= PER_PATH_LIMIT:
            continue
        seen[key] += 1
        keep.append(rec)
    keep.reverse()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".jsonl.tmp")   # 書くのは自分の機体のファイルだけ
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
    os.replace(tmp, STORE)                            # 途中で落ちても壊れた状態を残さない


def load():
    """{relpath: [クエリ文, ...]} を返す。pj-ngram.score_entries の extra に渡す。

    併合後にも PER_PATH_LIMIT を効かせる。各機のファイルは自分の中で間引かれているが、
    3機分を足せば 1 プロジェクトあたり最大 30 件になり、よく使うプロジェクトの
    テキストが太って何にでも当たるようになるため。
    """
    out = {}
    for rec in reversed(_read()):                     # _read() は ts 昇順なので新しい順に見る
        qs = out.setdefault(rec["path"], [])
        if len(qs) >= PER_PATH_LIMIT:
            continue
        qs.append(rec["q"])
    for qs in out.values():
        qs.reverse()
    return out


def record(query, path):
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LEN:
        return False
    # 自分のファイルだけを基準にする。_read()（全機併合）を書き戻すと、他機の記録が
    # 自分のファイルへ複製されて二重に持つことになる。
    records = [r for r in _read_own() if not (r["q"] == query and r["path"] == path)]
    records.append({"q": query, "path": path, "ts": int(time.time())})
    _write(records)
    return True


def forget(query, path):
    """誤記録を取り消す。他機のファイルにある分も消す。

    同期先は共有ディレクトリなので他機のファイルも書ける。持ち主の機が次に record
    したときは自分のファイルの現在値を基準に書くため、消した記録が復活することはない。
    """
    query = (query or "").strip()
    removed = False
    for f in _store_files():
        before = _read_file(f)
        after = [r for r in before if not (r["q"] == query and r["path"] == path)]
        if len(after) == len(before):
            continue
        tmp = f.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in after),
                       encoding="utf-8")
        os.replace(tmp, f)
        removed = True
    return removed


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 64
    cmd = argv[1]
    if cmd == "record" and len(argv) >= 4:
        ok = record(argv[2], argv[3].strip("/"))
        return 0 if ok else 3                          # 3 = 短すぎて憶えなかった
    if cmd == "forget" and len(argv) >= 4:
        return 0 if forget(argv[2], argv[3].strip("/")) else 3
    if cmd == "list":
        target = argv[2].strip("/") if len(argv) >= 3 else None
        data = load()
        for path in sorted(data):
            if target and path != target:
                continue
            print(f"{path}  ({len(data[path])}件)")
            for q in data[path]:
                print(f"    {q}")
        return 0
    sys.stderr.write(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
