#!/usr/bin/env python3
"""写しであることを確かめてから消す。唯一の中身は消さずに残す。

渡したパスを、隠しファイルと gitignore されたものまで含めてファイル1つずつに展開し、
ファイルごとに「同じ中身がほかにあるか」を消す直前に確かめる。判定する対象と消す対象が
構造上いつも同じになるので、「調べた範囲より消した範囲が広かった」「調べた後に状態が
変わっていた」という誤りが起きない。設計と経緯は hooks/README.md「削除の検査」。

同じ中身があるとみなす場所:
  - そのファイルがある git リポジトリの、上流（refs/remotes）から辿れる履歴。今の版でなくてもよい
  - --against <パス>        手元の別のファイル・フォルダ（配下の全ファイル）
  - --against <ホスト>:<パス>  ssh で届く別の端末のファイル・フォルダ
  - --against md5:<値>・sha1:<値>・sha256:<値>  Google Drive（md5Checksum）・Box（sha1）などが返すハッシュ

分類と扱い:
  - 写し：上のどこかに同じ中身がある。消す
  - 生成物：OS やツールが作り直すもの（.DS_Store・~$*・__pycache__ など）。消す
  - 唯一の中身：どこにも同じ中身が無い。残して一覧を出す。消すのは --unique-ok で理由を渡し、
    ユーザーが一覧を見て承認したときだけ

--unique-ok <TSV> は 1 行 1 件で「パス<TAB>何か<TAB>消す理由<TAB>消してよい根拠」。TSV に行の
ある唯一の中身だけを消し、行の無いものは残す。--dry-run と組み合わせると承認用の一覧を出す
（ユーザーに見せる一覧と、消す範囲が同じ TSV から決まる）。

  python3 dedup-rm.py [--dry-run] [--against <場所>]... [--unique-ok <TSV>] <パス>...

環境変数 DEDUP_RM_LEDGER に TSV のパスがあれば、消したファイルを「日時・端末・パス・種別・根拠」の
1 行ずつで追記する（パスは ~/Projects の下なら相対）。Unison の異常報告が、外れたパスを
「意図して消したか」で仕分けるのに使う。生成物は記録しない。

消した後、渡したフォルダの中で空になったフォルダも消す。終了コードは、全部消せたら 0、
唯一の中身を残したら 3、使い方の誤りは 2。最後の行が件数のまとめで、報告にはそれを使う。
"""
import argparse
import datetime
import hashlib
import os
import shlex
import subprocess
import sys

JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
JUNK_PREFIXES = ("~$", "._", ".~lock.")
JUNK_SUFFIXES = (".pyc",)
JUNK_DIRS = {"__pycache__"}
HASHES = ("md5", "sha1", "sha256")

REMOTE_HASHER = r"""
import hashlib, os, sys
root = os.path.expanduser(sys.argv[1])
def h(p):
    d = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            d.update(b)
    return d.hexdigest()
if os.path.isfile(root):
    print(h(root) + '\t' + root)
else:
    for dp, dn, fn in os.walk(root):
        for n in fn:
            p = os.path.join(dp, n)
            if os.path.isfile(p) and not os.path.islink(p):
                print(h(p) + '\t' + p)
"""


def is_junk(path):
    name = os.path.basename(path)
    parts = path.split(os.sep)
    return (name in JUNK_NAMES or name.startswith(JUNK_PREFIXES) or name.endswith(JUNK_SUFFIXES)
            or any(p in JUNK_DIRS for p in parts))


def expand(paths):
    """パスをファイル（とシンボリックリンク）1つずつに展開する。フォルダの symlink は辿らない。"""
    out, dirs = [], []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.islink(p) or os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            dirs.append(p)
            for dp, dn, fn in os.walk(p, followlinks=False):
                out += [os.path.join(dp, n) for n in fn]
                out += [os.path.join(dp, n) for n in dn if os.path.islink(os.path.join(dp, n))]
        else:
            sys.exit(f"dedup-rm: 見つからない：{p}")
    return sorted(set(out)), dirs


def file_hashes(path, algos):
    ds = {a: hashlib.new(a) for a in algos}
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            for d in ds.values():
                d.update(b)
    return {a: d.hexdigest() for a, d in ds.items()}


def git(args, cwd, input=None):
    r = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def git_copies(files):
    """上流から辿れる履歴に同じ中身（blob）があるファイル → 根拠の文字列。"""
    by_repo = {}
    for f in files:
        d = os.path.dirname(f)
        top = git(["rev-parse", "--show-toplevel"], d)
        if top and f"{os.sep}.git{os.sep}" not in f:
            by_repo.setdefault(top.strip(), []).append(f)
    found = {}
    for top, fs in by_repo.items():
        if not git(["for-each-ref", "--count=1", "refs/remotes"], top):
            continue
        blobs = git(["hash-object", "--stdin-paths"], top, input="\n".join(fs) + "\n")
        if blobs is None:
            continue
        want = dict(zip(blobs.split(), fs))
        reachable = subprocess.Popen(["git", "rev-list", "--objects", "--remotes"], cwd=top, stdout=subprocess.PIPE, text=True)
        for line in reachable.stdout:
            oid = line[:line.find(" ")] if " " in line else line.strip()
            if oid in want:
                found[want.pop(oid)] = f"git の上流の履歴（{os.path.basename(top)}）"
                if not want:
                    break
        reachable.stdout.close()
        reachable.kill()
    return found


def against_index(sources, targets):
    """--against の各場所から、sha256 → 根拠。対象そのものは含めない（自分自身との一致を除く）。"""
    idx, hashes = {}, []
    real_targets = {os.path.realpath(t) for t in targets}
    for s in sources:
        algo, _, val = s.partition(":")
        if algo in HASHES and val:
            hashes.append((algo, val.lower()))
            continue
        local = os.path.abspath(os.path.expanduser(s))
        if os.path.exists(local):
            files, _ = expand([local])
            for f in files:
                if os.path.realpath(f) in real_targets or os.path.islink(f):
                    continue
                idx.setdefault(file_hashes(f, ["sha256"])["sha256"], f)
            continue
        host, sep, rpath = s.partition(":")
        if not sep or "/" in host:
            sys.exit(f"dedup-rm: --against の場所が見つからない：{s}")
        r = subprocess.run(["ssh", host, "python3", "-", shlex.quote(rpath)], input=REMOTE_HASHER, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"dedup-rm: {host} の {rpath} を読めない：{r.stderr.strip()[:200]}")
        for line in r.stdout.splitlines():
            h, _, p = line.partition("\t")
            idx.setdefault(h, f"{host}:{p}")
    return idx, hashes


def read_tsv(path):
    rows = {}
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) != 4 or not all(c.strip() for c in cols):
                sys.exit(f"dedup-rm: {path} の {n} 行目は「パス・何か・消す理由・根拠」の4列でない")
            rows[os.path.abspath(os.path.expanduser(cols[0]))] = cols[1:]
    return rows


def record(rows):
    """DEDUP_RM_LEDGER があれば、消したファイルを台帳へ追記する。失敗しても削除は止めない。"""
    path = os.environ.get("DEDUP_RM_LEDGER")
    if not path or not rows:
        return
    root = os.path.expanduser("~/Projects") + os.sep
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    host = os.uname().nodename.split(".")[0].lower()
    try:
        with open(os.path.expanduser(path), "a", encoding="utf-8") as f:
            for f_path, kind, basis in rows:
                rel = f_path[len(root):] if f_path.startswith(root) else f_path
                f.write("\t".join([stamp, host, rel, kind, basis.replace("\t", " ").replace("\n", " ")]) + "\n")
    except OSError as e:
        print(f"（台帳に書けなかった：{e}）")


def shown(p):
    cwd = os.getcwd()
    return os.path.relpath(p, cwd) if p.startswith(cwd + os.sep) else p


def meta(p):
    st = os.lstat(p)
    size = st.st_size
    s = f"{size}B" if size < 1024 else f"{size / 1024:.0f}KB" if size < 1 << 20 else f"{size / (1 << 20):.1f}MB"
    return f"{s}、{datetime.datetime.fromtimestamp(st.st_mtime):%Y-%m-%d}"


def main():
    ap = argparse.ArgumentParser(description="写しであることを確かめてから消す。唯一の中身は残す。")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--against", action="append", default=[])
    ap.add_argument("--unique-ok")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files, dirs = expand(a.paths)
    idx, hashes = against_index(a.against, files)
    algos = ["sha256", *sorted({h for h, _ in hashes})]
    ok_rows = read_tsv(a.unique_ok) if a.unique_ok else {}

    copies, unique = [], []
    rest = [f for f in files if not is_junk(f)]
    junk = [f for f in files if is_junk(f)]
    gitc = git_copies([f for f in rest if not os.path.islink(f)])
    for f in rest:
        why = gitc.get(f)
        if not why and not os.path.islink(f):
            hs = file_hashes(f, algos)
            why = idx.get(hs["sha256"])
            why = why if why else next((f"{h} 指定の {v[:12]}…" for h, v in hashes if hs[h] == v), None)
        (copies.append((f, why)) if why else unique.append(f))

    approved = [f for f in unique if f in ok_rows]
    kept = [f for f in unique if f not in ok_rows]
    for p in ok_rows:
        if p not in unique:
            print(f"（--unique-ok の行は唯一の中身に当たらないので使わない：{shown(p)}）")

    verb = "消す予定" if a.dry_run else "消した"
    if copies:
        print(f"写し（{verb}）{len(copies)}件")
        for f, why in copies:
            print(f"  {shown(f)} ← {why}")
    if junk:
        print(f"生成物（{verb}）{len(junk)}件")
        for f in junk:
            print(f"  {shown(f)}")
    if approved:
        print(f"唯一の中身（承認により{verb}）{len(approved)}件")
        for f in approved:
            what, reason, basis = ok_rows[f]
            print(f"- {shown(f)}（{meta(f)}）\n  何か：{what}\n  消す理由：{reason}\n  消してよい根拠：{basis}")
    if kept:
        print(f"唯一の中身（残した）{len(kept)}件。消すなら --unique-ok に理由を書き、承認を取る")
        for f in kept:
            print(f"  {shown(f)}（{meta(f)}）")

    if not a.dry_run:
        for f in [c for c, _ in copies] + junk + approved:
            os.remove(f)
        record([(f, "写し", why) for f, why in copies]
               + [(f, "承認", ok_rows[f][1]) for f in approved])
        for d in dirs:
            for dp, dn, fn in os.walk(d, topdown=False):
                if not os.listdir(dp):
                    os.rmdir(dp)
    print(f"まとめ：{verb} 写し{len(copies)}・生成物{len(junk)}・承認{len(approved)}／残した 唯一の中身{len(kept)}")
    sys.exit(3 if kept else 0)


if __name__ == "__main__":
    main()
