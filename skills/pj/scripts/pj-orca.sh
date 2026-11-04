#!/usr/bin/env bash
# pj-orca.sh — pj の行き先を Orca の作業場にする起動プリミティブ。
#
# pj-open.sh（iTerm2 か Windows Terminal の新しい窓）と pj()（今の端末）は、Orca が動いていれば
# これを呼ぶ。どの入口（pj・/pj・ホットキーの窓・pj リンク）から来ても、セッションが Orca の一覧に
# 載るようにするため。入れ方と設定は claude-toolkit の tools/orca/README.md。
#
#   pj-orca.sh --check                       Orca で開けるなら 0、開けないなら 1
#   pj-orca.sh <relpath> [--agent claude|codex] [--prompt-file <file>] [--request "<原文>"]
#   pj-orca.sh --resume <ID> --dir <dir> [--prompt-file <file>]
#   pj-orca.sh --focus <dir> [--match "<最後の依頼の文>"] [--prompt-file <file>]
#
# <relpath> は ~/Projects からの相対パス。リポジトリの下位フォルダ（utokyo/CRPC/education
# など）なら、作業場を作ってから端末で `cd <下位フォルダ> && claude` を実行する。Orca の
# エージェント起動は作業場の最上位でしか始められず、下位フォルダの CLAUDE.md が最初に
# 読まれなくなるため。最上位なら Orca のエージェント起動をそのまま使う。
#
# Windows（2026-11-04、311C4W991 で検証）では、最上位でも Orca のエージェント起動を使わず、
# 作業場を作ってから端末で起動用の .ps1 を実行する。.ps1 は今のセッションから受け継いだ
# CLAUDE_* の変数を外し（pj-platform.sh の PJ_PS_CLEAN_ENV）、依頼文をファイルから読む。
# Orca を Claude Code の中から起動すると、Orca とその端末が CLAUDE_CODE_CHILD_SESSION を
# 受け継ぎ、開いた claude が記録を残さないため。端末のシェルは pwsh（無ければ Windows PowerShell）。
#
# --request は pj に打った原文。行き先の名前だけでなく用件を含んでいれば、振り分けの1行を
# 付けて最初の指示として渡す（判定は pj-router.py request。開いた先で用件を打ち直さないため）。
#
# --resume は claude-sessions.py open の Orca 版。<dir> が Orca の作業場の中にあればその作業場に
# 端末を足して `claude --resume <ID>` を実行し、作業場が片付いていれば（<dir> が ~/Projects の
# 中なら）新しい作業場を作って同じ下位フォルダで再開する。--resume <ID> は記録をどのフォルダ
# からでも読める（scripts/claude-sessions/README.md）。
#
# --focus は、<dir> を含む作業場で動いている claude の端末を前に出す。同じ作業場に claude が
# 複数あれば、Orca が覚えている最後の依頼の文が --match と合うものを選ぶ。--prompt-file が
# あれば、その端末へ依頼文を送る。見つからなければ 1 を返す。
#
# Orca に登録していないリポジトリは、その場で登録する。作業場は毎回新しく作る（pj の
# 「本体が空いていれば本体で開く」は持ち込まない。片付けは wt-finish.sh が受け持つ）。
#
# 使わない設定: PJ_LAUNCHER=iterm を環境変数に置くと --check が常に 1 を返し、従来どおり
# iTerm2（Windows では Windows Terminal）で開く。
#
# 終了コード: 0=起動した / 1=Orca で開けない（呼び出し側は iTerm2 へ戻る）/ 2=引数不正
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/pj-platform.sh"
PROJECTS="$HOME/Projects"
ORCA="$(pj_find_orca)"

available() {
  [[ "${PJ_LAUNCHER:-}" != "iterm" ]] || return 1
  [[ -n "$ORCA" && -x "$ORCA" ]] || return 1
  "$ORCA" status --json 2>/dev/null | grep -q '"reachable": true'
}

if [[ "${1:-}" == "--check" ]]; then
  available
  exit $?
fi

relpath="" agent="claude" prompt_file="" request="" resume="" dir="" focus="" match=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) agent="${2:-}"; shift 2 ;;
    --prompt-file) prompt_file="${2:-}"; shift 2 ;;
    --request) request="${2:-}"; shift 2 ;;
    --resume) resume="${2:-}"; shift 2 ;;
    --dir) dir="${2:-}"; shift 2 ;;
    --focus) focus="${2:-}"; shift 2 ;;
    --match) match="${2:-}"; shift 2 ;;
    -*) echo "pj-orca.sh: 不明なオプション: $1" >&2; exit 2 ;;
    *) if [[ -z "$relpath" ]]; then relpath="$1"; shift; else echo "pj-orca.sh: 引数が多すぎます: $1" >&2; exit 2; fi ;;
  esac
done
[[ "$agent" == "claude" || "$agent" == "codex" ]] || { echo "pj-orca.sh: --agent は claude か codex" >&2; exit 2; }
[[ -z "$prompt_file" || -f "$prompt_file" ]] || { echo "pj-orca.sh: 依頼文のファイルが無い: $prompt_file" >&2; exit 2; }
[[ -z "$resume" || -n "$dir" ]] || { echo "pj-orca.sh: --resume には --dir が要る" >&2; exit 2; }
[[ -n "$relpath" || -n "$resume" || -n "$focus" ]] || { echo "pj-orca.sh: <relpath>・--resume・--focus のどれかが要る" >&2; exit 2; }
[[ -z "$dir" ]] || dir="$(pj_posix "$dir")"
[[ -z "$focus" ]] || focus="$(pj_posix "$focus")"
available || exit 1

# 依頼文はシェルの文字列に埋め込まず、起動するシェルが読む（日本語・引用符の崩れを避ける）。
# 呼び出し側のファイルは起動後に消す約束なので、端末が読むための写しを別に作る
keep_prompt() {
  [[ -n "$prompt_file" ]] || return 0
  local keep
  keep="$(mktemp "${TMPDIR:-/tmp}/pj-orca-prompt-XXXXXX")"
  cp "$prompt_file" "$keep"
  printf '%s\n' "$keep"
}

# 端末で実行するコマンド。<dir> は cd する先（空なら端末の既定の場所）、残りは claude 等とその引数。
# Mac は zsh の1行、Windows は起動用の .ps1 を書いて、それを呼ぶ1行を返す
launch_line() {  # <dir> <program> [args...]
  local d="$1" prog="$2"; shift 2
  local keep; keep="$(keep_prompt)"
  if (( ! PJ_WIN )); then
    local line="${d:+cd $(printf %q "$d") && }$prog"
    local a; for a in "$@"; do line+=" $(printf %q "$a")"; done
    [[ -n "$keep" ]] && line+="$(printf ' "$(cat %q; rm -f %q)"' "$keep" "$keep")"
    printf '%s\n' "$line"
    return 0
  fi
  psq() { local s="${1//\'/\'\'}"; printf "'%s'" "$s"; }
  local ps1; ps1="$(mktemp "${TMPDIR:-/tmp}/pj-orca-XXXXXX").ps1"
  {
    printf '\xEF\xBB\xBF'
    printf 'Remove-Item -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue\n'
    printf '%s\n' "$PJ_PS_CLEAN_ENV"
    [[ -n "$d" ]] && printf 'Set-Location -LiteralPath %s\n' "$(psq "$(pj_win "$d")")"
    local args="" a; for a in "$@"; do args+=" $(psq "$a")"; done
    if [[ -n "$keep" ]]; then
      local pf; pf="$(psq "$(pj_win "$keep")")"
      printf '$p = Get-Content -Raw -Encoding utf8 -LiteralPath %s\n' "$pf"
      printf 'Remove-Item -LiteralPath %s -ErrorAction SilentlyContinue\n' "$pf"
      printf '%s%s $p\n' "$prog" "$args"
    else
      printf '%s%s\n' "$prog" "$args"
    fi
  } > "$ps1"
  printf "& %s\n" "$(psq "$(pj_win "$ps1")")"
}

orca_json() { "$ORCA" "$@" --json 2>/dev/null; }

done_msg() {
  local note=""
  [[ -n "$prompt_file" ]] && { note="（依頼文を渡した）"; rm -f "$prompt_file"; }
  if (( PJ_WIN )); then
    # 動いている Orca.exe をもう一度起動すると、新しく立ち上がらずに今の窓が前に出る
    local app; app="$(pj_win "$(dirname "$ORCA")/../../Orca.exe")"
    cmd.exe //c start "" "$app" >/dev/null 2>&1 || true
  else
    open -a Orca 2>/dev/null || true
  fi
  echo "$1$note" >&2
  exit 0
}

# Orca の JSON を読む小さな Python に前置きする、パスを比べる部品。引数は環境変数で渡す
# （日本語をコードに埋めない）。Windows では区切りと大文字・小文字を揃えて比べる
PY_PATH='import os, sys
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
def norm(p): return os.path.normcase(os.path.realpath(p))
def under(child, parent):
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)
'

# ---- --focus: 動いている claude の端末を前に出す ----
if [[ -n "$focus" ]]; then
  handle="$(orca_json terminal list | FOCUS="$(pj_win "$focus")" MATCH="$match" ORCA="$ORCA" python3 -c "$PY_PATH"'
import json, subprocess, sys
d = os.environ["FOCUS"]
ts = [t for t in json.load(sys.stdin)["result"]["terminals"]
      if t.get("agentIdentity") == "claude" and t.get("connected") and under(d, t["worktreePath"])]
m = os.environ.get("MATCH", "").strip()[:40]
if len(ts) > 1 and m:
    ps = subprocess.run([os.environ["ORCA"], "worktree", "ps", "--json"], capture_output=True, text=True, encoding="utf-8").stdout
    keys = set()
    for w in json.loads(ps or "{}").get("result", {}).get("worktrees", []):
        for a in w.get("agents", []):
            if (a.get("prompt") or "").strip()[:40] == m:
                keys.add(a.get("paneKey"))
    ts = [t for t in ts if str(t.get("tabId")) + ":" + str(t.get("leafId")) in keys] or ts
ts.sort(key=lambda t: -(t.get("lastOutputAt") or 0))
print(ts[0]["handle"] if ts else "")
')"
  [[ -n "$handle" ]] || exit 1
  "$ORCA" terminal switch --terminal "$handle" >/dev/null 2>&1 || exit 1
  if [[ -n "$prompt_file" ]]; then
    "$ORCA" terminal send --terminal "$handle" --text "$(cat "$prompt_file")" --enter >/dev/null 2>&1 \
      || { echo "pj-orca.sh: 端末は前に出したが、依頼文を送れなかった: $prompt_file" >&2; exit 0; }
  fi
  done_msg "→ 動いているセッションを Orca で前に出しました"
fi

# 作業場を新しく作り、パスを出す。登録していないリポジトリはその場で登録する
new_workspace() {  # <root> <name> [extra args...]
  local root; root="$(pj_win "$1")"; local name="$2"; shift 2
  if ! orca_json repo list | ROOT="$root" python3 -c "$PY_PATH"'
import json, sys
sys.exit(0 if any(norm(r["path"]) == norm(os.environ["ROOT"]) for r in json.load(sys.stdin)["result"]["repos"]) else 1)
'; then
    "$ORCA" repo add --path "$root" --json >/dev/null 2>&1 \
      || { echo "pj-orca.sh: Orca への登録に失敗した: $root" >&2; return 1; }
  fi
  orca_json worktree create --repo "path:$root" --name "$name" --no-parent --activate "$@" \
    | python3 -c "$PY_PATH"'
import json
print(json.load(sys.stdin)["result"]["worktree"]["path"])
'
}

run_in() {  # <worktree> <command>
  local sh=()
  if (( PJ_WIN )); then
    if command -v pwsh.exe >/dev/null 2>&1; then sh=(--shell pwsh.exe); else sh=(--shell powershell.exe); fi
  fi
  "$ORCA" terminal create --worktree "path:$1" --title claude "${sh[@]}" --command "$2" --focus --json >/dev/null 2>&1 \
    || { echo "pj-orca.sh: 端末の起動に失敗した（作業場は残っている: $1）" >&2; return 1; }
}

# ---- --resume: 作業場の中で claude --resume ----
if [[ -n "$resume" ]]; then
  [[ "$resume" =~ ^[0-9a-f-]+$ ]] || { echo "pj-orca.sh: セッション ID の形ではない: $resume" >&2; exit 2; }
  wt="$(orca_json worktree list | DIR="$(pj_win "$dir")" python3 -c "$PY_PATH"'
import json, sys
d = os.environ["DIR"]
ws = [w["path"] for w in json.load(sys.stdin)["result"]["worktrees"]
      if not w.get("isMainWorktree") and under(d, w["path"])]
print(max(ws, key=len) if ws else "")
')"
  if [[ -n "$wt" && -d "$dir" ]]; then
    run_in "$wt" "$(launch_line "$dir" claude --resume "$resume")" || exit 1
    done_msg "→ セッション $resume を Orca の作業場 $(basename "$(pj_posix "$wt")") で再開しました"
  fi
  [[ "$dir" == "$PROJECTS/"* ]] || { echo "pj-orca.sh: Orca の作業場でも ~/Projects の中でもない: $dir" >&2; exit 1; }
  relpath="${dir#"$PROJECTS"/}"
fi

relpath="${relpath#/}"; relpath="${relpath%/}"
d="$PROJECTS/$relpath"
root="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)" || { echo "pj-orca.sh: git リポジトリではない: $d" >&2; exit 1; }
sub="$(git -C "$d" rev-parse --show-prefix 2>/dev/null)"; sub="${sub%/}"
name="$(basename "$relpath")-$(date +%m%d-%H%M%S)"

if [[ -n "$resume" ]]; then
  wt="$(new_workspace "$root" "$name")" && [[ -n "$wt" ]] || { echo "pj-orca.sh: 作業場の作成に失敗した" >&2; exit 1; }
  run_in "$wt" "$(launch_line "${sub:+$(pj_posix "$wt")/$sub}" claude --resume "$resume")" || exit 1
  done_msg "→ セッション $resume を Orca の新しい作業場 $name で再開しました"
fi

if [[ -n "$request" ]]; then
  prompt_file="$(python3 "$SCRIPT_DIR/pj-router.py" request "$request" "$relpath" ${prompt_file:+--body-file "$prompt_file"} --file)"
fi

if [[ -z "$sub" ]] && (( ! PJ_WIN )); then
  args=(--agent "$agent" --surface terminal)
  [[ -n "$prompt_file" ]] && args+=(--prompt "$(cat "$prompt_file")")
  new_workspace "$root" "$name" "${args[@]}" >/dev/null || { echo "pj-orca.sh: 作業場の作成に失敗した" >&2; exit 1; }
else
  wt="$(new_workspace "$root" "$name")" && [[ -n "$wt" ]] || { echo "pj-orca.sh: 作業場の作成に失敗した" >&2; exit 1; }
  run_in "$wt" "$(launch_line "$(pj_posix "$wt")${sub:+/$sub}" "$agent")" || exit 1
fi
done_msg "→ $relpath を Orca の作業場 $name で起動しました"
