#!/usr/bin/env python3
"""PreToolUse（Bash）：守る場所（既定は ~/Projects）のファイルを直接消すコマンドを止め、dedup-rm へ回す。

消してよいかの判定はしない。判定は ~/.claude/scripts/dedup-rm.py が、消すファイルそのものについて
消す直前に行う。このフックの役目は、その判定を回り道されないようにすることだけ。

止めるもの（ssh の先のコマンド、bash -c の中も見る）:
  - rm・unlink で守る場所の中を指すもの
  - find の -delete・-exec rm で、探す起点が守る場所の中のもの
  - git clean -f で、作業ディレクトリが守る場所の中のもの
  - mv で、守る場所の中から外へ出すもの
  - rsync --delete で、送り先が守る場所の中のもの
  - unison の -force（場所を問わない。片側に無いものを反対側で消すミラーリングのため）
生成物（.DS_Store・~$*・__pycache__・node_modules）と .git の中の *.lock は通す。

守る場所は環境変数 CLAUDE_PROTECTED_ROOTS（: 区切り）で変えられる。設計と経緯は hooks/README.md「削除の検査」。
"""
import json
import os
import re
import shlex
import sys

HOME = os.path.expanduser("~")
ROOTS = [os.path.abspath(os.path.expanduser(r)) for r in os.environ.get("CLAUDE_PROTECTED_ROOTS", "~/Projects").split(":") if r]
GENERATED = re.compile(r"(^|/)(\.DS_Store|~\$[^/]*|__pycache__(/.*)?|node_modules(/.*)?|[^/]*\.pyc|\.git/.*\.lock)$")
OPS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
SSH_OPTS_WITH_ARG = set("bcDEeFIiJLlmOopQRSWw")
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
UNKNOWN = "<変数で決まる場所>"
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "nice", "exec"}


def protected(path, cwd):
    # 変数で決まる対象は、作業ディレクトリが守る場所の中なら守る場所の中とみなす
    if UNKNOWN in path:
        return protected(".", cwd)
    p = os.path.expanduser(path)
    p = os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd, p))
    if GENERATED.search(p):
        return False
    return any(p == r or p.startswith(r + os.sep) for r in ROOTS)


def strip_heredocs(text):
    """ヒアドキュメントの本文は、ファイルへ書く中身であってコマンドではないので外す。"""
    out, end = [], None
    for line in text.split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            end = m.group(2)
    return "\n".join(out)


def commands(text):
    lex = shlex.shlex(strip_heredocs(text).replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    cur = []
    for t in lex:
        if t in OPS:
            if cur:
                yield cur
            cur = []
        else:
            cur.append(t)
    if cur:
        yield cur


def args_of(tokens):
    out, opts_done = [], False
    for t in tokens:
        if t == "--" and not opts_done:
            opts_done = True
        elif opts_done or not t.startswith("-"):
            out.append(t)
    return out


def check(text, cwd, where=""):
    hits, env = [], {}
    for tok in commands(text):
        # 同じコマンドの中の代入（B=~/Backups/x）だけを覚えて展開する。展開できない変数は、行き先の分からない場所とみなす
        if tok and all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) for t in tok):
            env.update(t.split("=", 1) for t in tok)
            continue
        tok = [VAR.sub(lambda m: env.get(m.group(1), HOME if m.group(1) == "HOME" else UNKNOWN), t) for t in tok]
        while tok and (tok[0] in WRAPPERS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok[0])):
            tok = tok[1:]
        if not tok:
            continue
        name, rest = os.path.basename(tok[0]), tok[1:]
        if name == "cd" and rest and rest[0] != "-":
            d = os.path.expanduser(rest[0])
            cwd = os.path.normpath(d if os.path.isabs(d) else os.path.join(cwd, d))
        elif name == "ssh":
            i = 0
            while i < len(rest) and rest[i].startswith("-"):
                i += 2 if rest[i][-1] in SSH_OPTS_WITH_ARG and len(rest[i]) == 2 else 1
            if i + 1 < len(rest):
                hits += check(" ".join(rest[i + 1:]), HOME, f"{rest[i]} で ")
        elif name in ("bash", "sh", "zsh") and any(re.fullmatch(r"-[a-z]*c[a-z]*", t) for t in rest):
            i = next(k for k, t in enumerate(rest) if re.fullmatch(r"-[a-z]*c[a-z]*", t))
            if i + 1 < len(rest):
                hits += check(rest[i + 1], cwd, where)
        elif name in ("rm", "unlink"):
            hits += [f"{where}{name} {t}" for t in args_of(rest) if protected(t, cwd)]
        elif name == "find" and ("-delete" in rest or any(rest[k] == "-exec" and k + 1 < len(rest) and os.path.basename(rest[k + 1]) == "rm" for k in range(len(rest)))):
            starts = []
            for t in rest:
                if t.startswith(("-", "(", "!")):
                    break
                starts.append(t)
            hits += [f"{where}find {t} の削除" for t in (starts or ["."]) if protected(t, cwd)]
        elif name == "xargs" and any(os.path.basename(t) == "rm" for t in rest) and protected(".", cwd):
            hits.append(f"{where}xargs rm（{cwd}）")
        elif name == "git" and "clean" in rest and any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*|--force", t) for t in rest):
            d = cwd
            if "-C" in rest and rest.index("-C") + 1 < len(rest):
                d = os.path.join(cwd, os.path.expanduser(rest[rest.index("-C") + 1]))
            if protected(".", d):
                hits.append(f"{where}git clean（{d}）")
        elif name == "mv":
            a = args_of(rest)
            if "-t" in rest and rest.index("-t") + 1 < len(rest):
                dest, srcs = rest[rest.index("-t") + 1], [x for x in a if x != rest[rest.index("-t") + 1]]
            else:
                dest, srcs = (a[-1], a[:-1]) if len(a) >= 2 else (None, [])
            if dest and (UNKNOWN in dest or not protected(dest, cwd)):
                hits += [f"{where}mv {s} → {dest}（守る場所の外へ出す）" for s in srcs if protected(s, cwd)]
        elif name == "rsync" and any(t.startswith("--delete") for t in rest):
            a = args_of(rest)
            if a and ":" not in a[-1] and protected(a[-1], cwd):
                hits.append(f"{where}rsync --delete → {a[-1]}")
        elif name == "unison" and any(t.startswith("-force") for t in rest):
            hits.append(f"{where}unison -force（ミラーリングで片側に無いものを消す）")
    return hits


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()
    try:
        hits = check(command, cwd)
    except (ValueError, StopIteration):
        # 引用符が閉じていない等で読めないときは、消すコマンドらしければ確認に回す
        if re.search(r"(^|[\s;&|('\"])(rm|unlink|git\s+clean)\s", command) and any(r in command or "Projects" in command for r in ROOTS):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                  "permissionDecisionReason": "削除を含むかもしれないコマンドを読み切れなかった。消すなら dedup-rm を使う"}}, ensure_ascii=False))
        return
    if not hits:
        return
    reason = (
        "守る場所のファイルを直接消すコマンドなので止めた：" + "、".join(hits[:5]) + ("ほか" if len(hits) > 5 else "") + "。"
        "消すときは python3 ~/.claude/scripts/dedup-rm.py <パス>... を使う（別の端末なら ssh <ホスト> python3 ~/.claude/scripts/dedup-rm.py ...）。"
        "同じ中身がほかにある写しと生成物だけが消え、唯一の中身は残って一覧が出る。"
        "唯一の中身を消すのは、--unique-ok で1件ずつ「何か・消す理由・消してよい根拠」を渡した一覧をユーザーに見せ、承認を取ってから。"
        "作業用の一時ファイルは守る場所の外（スクラッチ）に置く。"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
