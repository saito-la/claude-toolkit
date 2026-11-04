#!/usr/bin/env bash
# wt-cleanup-later.sh — Windows で wt-finish.sh が裏で起動する、遅らせた片付け。
#
#   bash wt-cleanup-later.sh <worktree> <本体> <ブランチ> <既定ブランチ> [<待つ pid>...]
#
# Windows では動いているプロセスの作業フォルダを消せない。wt-finish.sh を呼んだ claude 自身と、
# Orca の端末のシェルが worktree の中に居るので、統合と push の直後には消せない。そこで、
# worktree の中の claude（<待つ pid>）がすべて終わるのを待ってから片付ける。
#
# 待っている間にその worktree で作業が続いた場合に備え、消す直前にもう一度確かめる。未コミットの
# 変更か、origin/<既定ブランチ> に無いコミットがあれば、何も消さずに終わる（統合されていない作業を
# 落とさない）。Orca の作業場（~/orca/workspaces/ の下）は Orca に消させ（端末のシェルも止まる）、
# pj の worktree（.claude/worktrees/ の下）は git で消す。
#
# 記録は ~/.cache/pj/wt-cleanup.log に1行ずつ足す。待つのは最長24時間。
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/pj-platform.sh"

wt="$(pj_posix "${1:?worktree}")" main="$(pj_posix "${2:?本体}")" branch="${3:?ブランチ}" default_branch="${4:?既定ブランチ}"
shift 4
log="$HOME/.cache/pj/wt-cleanup.log"
mkdir -p "$(dirname "$log")"
note() { printf '%s %s %s\n' "$(date '+%F %T')" "${wt/#$HOME/~}" "$*" >> "$log"; }

alive() {  # 渡した pid のうち、まだ動いているものがあれば 0
  local p list
  list="$(tasklist.exe //FO CSV //NH 2>/dev/null | cut -d, -f2 | tr -cd '0-9\n')"
  for p in "$@"; do grep -qx "$p" <<< "$list" && return 0; done
  return 1
}

deadline=$(( $(date +%s) + 86400 ))
while (( $# )) && alive "$@"; do
  (( $(date +%s) < deadline )) || { note "24時間待っても claude が終わらないので片付けない"; exit 0; }
  sleep 5
done
sleep 3   # 終わった claude の後始末（記録の書き出し）を待つ

[[ -d "$wt" ]] || { note "既に無い"; exit 0; }
if [[ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]]; then
  note "未コミットの変更があるので片付けない"; exit 0
fi
git -C "$main" fetch --quiet origin "$default_branch" 2>/dev/null || true
if [[ -n "$(git -C "$wt" log --oneline "origin/$default_branch..HEAD" 2>/dev/null)" ]]; then
  note "origin/$default_branch に無いコミットがあるので片付けない"; exit 0
fi

if [[ "$wt" == "$HOME/orca/workspaces/"* ]] && orca="$(pj_find_orca)" && [[ -n "$orca" ]] \
   && "$orca" status --json 2>/dev/null | grep -q '"reachable": true'; then
  # 1回目は、Orca が止めた端末のシェルがまだフォルダを掴んでいて失敗することがある（2026-11-04 実測。
  # 中のファイルは消え、空のフォルダが残る）。間を置いて数回までやり直す
  ok=0
  for t in 1 2 3 4 5; do
    if "$orca" worktree rm --worktree "path:$(pj_win "$wt")" --force --json 2>/dev/null | grep -q '"removed": true'; then
      ok=1; break
    fi
    sleep $(( t * 3 ))
  done
  (( ok )) || { note "Orca が作業場を消せなかった（5回）"; exit 0; }
  note "Orca の作業場を消した"
else
  git -C "$main" worktree remove "$wt" >/dev/null 2>&1 || { note "git worktree remove が失敗した"; exit 0; }
  note "worktree を消した"
fi
git -C "$main" worktree prune 2>/dev/null || true

if git -C "$main" show-ref --verify --quiet "refs/heads/$branch"; then
  if [[ -z "$(git -C "$main" log --oneline "origin/$default_branch..$branch" 2>/dev/null)" ]]; then
    git -C "$main" branch -D "$branch" >/dev/null 2>&1 && note "ブランチ $branch を消した"
  else
    note "ブランチ $branch は origin/$default_branch に未統合のコミットを持つので残した"
  fi
fi
