#!/usr/bin/env bash
# wt-finish.sh — wt-start.sh で作った worktree の成果を既定ブランチへ統合し、
# worktree とブランチを片付ける。worktree の中で実行する。
#
#   bash wt-finish.sh              # 統合して push、worktree を削除
#   bash wt-finish.sh --dry-run    # 何が起きるかだけ表示する（変更しない）
#   bash wt-finish.sh --keep       # 統合と push はするが worktree を残す
#
# 流れ:
#   1. 未コミットが無いことを確認（あれば中断。何をコミットするかは人が決める）
#   2. origin から既定ブランチを取得し、その上に rebase
#   3. 既定ブランチへ早送りマージして push
#   4. worktree とブランチを削除（--keep なら残す）
#
# 途中で失敗したら、その場で止めて何が起きたかを表示する。中断しても worktree は
# 消さないので、解決してから再実行すればよい。rebase 中に中断した場合は worktree の
# 中で `git rebase --continue` か `git rebase --abort` を実行してから再実行する。
#
# 設計は同じスキルの SKILL.md「worktree」。

set -euo pipefail

# Mac と Windows（Git Bash）の差は pj-platform.sh が吸収する。Windows では、動いているプロセスの
# 作業フォルダは消せない（このスクリプトを呼ぶ claude 自身と、Orca の端末のシェルが worktree の中に
# 居る）。そのため統合と push までをここで行い、worktree とブランチの片付けは、worktree の中の
# claude がすべて終わってから裏で行う（下の「4. 片付け」。2026-11-04、311C4W991 で検証）
source "$(dirname "${BASH_SOURCE[0]}")/pj-platform.sh"

die() { printf '%s\n' "wt-finish.sh: $*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

dry_run=0
keep=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --keep) keep=1 ;;
    *) die "不明なオプション: $arg" ;;
  esac
done

run() {
  if (( dry_run )); then
    printf '  [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "git リポジトリの中で実行する"

git_dir="$(git rev-parse --git-dir)"
common_dir="$(git rev-parse --git-common-dir)"
[[ "$git_dir" != "$common_dir" ]] || die "ここは worktree ではない（本体で実行しても統合するものが無い）"

WT_DIR="$(pj_posix "$(git rev-parse --show-toplevel)")"
# git-common-dir は本体の .git を指す。その親が本体の作業ツリー。
MAIN_REPO="$(cd "$(dirname "$(cd "$common_dir" && pwd)")" && pwd)"
branch="$(git rev-parse --abbrev-ref HEAD)"

# wt-start.sh の worktree は wt/ で始まるブランチを持つ。Orca の作業場は ~/orca/workspaces/ に
# 置かれ、ブランチは Orca の設定の接頭辞（tosh13/ など）になるので、置き場で見分ける。
# 2026-11-04 まで wt/ だけを通しており、Orca の作業場ではセッション終了の統合が必ず止まった。
if [[ "$branch" != wt/* ]]; then
  [[ "$WT_DIR" == "$HOME/orca/workspaces/"* && "$branch" != "HEAD" ]] \
    || die "wt/ で始まらないブランチ（${branch}）。wt-start.sh か Orca で作った worktree で実行する"
fi

say "== worktree の統合 =="
say "  worktree : ${WT_DIR/#$HOME/~}"
say "  ブランチ : $branch"
say "  本体     : ${MAIN_REPO/#$HOME/~}"

# 1. 未コミットの確認。ここで自動コミットはしない。何を含めるかは人が決めることで、
#    自動化すると「意図しないものが入る」という worktree 導入の目的そのものを裏切る。
if [[ -n "$(git status --porcelain)" ]]; then
  say ""
  say "未コミットの変更がある。先にコミットするか退避してから再実行する:"
  git status --short | sed 's/^/  /'
  exit 1
fi

default_branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
[[ -n "$default_branch" ]] || default_branch=main

# 統合するコミットが無ければ、作っただけで終わった worktree。片付けるだけでよい。
ahead="$(git rev-list --count "origin/$default_branch..HEAD" 2>/dev/null || echo 0)"
if [[ "$ahead" == "0" ]]; then
  say ""
  say "$default_branch より進んだコミットが無い。統合するものは無いので片付けだけ行う"
else
  say ""
  say "統合するコミット（$ahead 件）:"
  git log --oneline "origin/$default_branch..HEAD" | sed 's/^/  /'
fi

# 2. 最新の既定ブランチの上に載せ替える。並行作業の相手が先に push している場合、
#    ここで初めて衝突が分かる。
say ""
say "origin/$default_branch を取得して rebase する"
run git fetch --quiet origin "$default_branch"

if [[ "$ahead" != "0" ]]; then
  if (( dry_run )); then
    printf '  [dry-run] git rebase origin/%s\n' "$default_branch"
  elif ! git rebase "origin/$default_branch"; then
    say ""
    say "rebase が衝突した。この worktree の中で解決してから再実行する:"
    say "  解決する  : 衝突ファイルを直して git add → git rebase --continue"
    say "  やり直す  : git rebase --abort"
    exit 1
  fi
fi

# 3. worktree から直接 push する。本体の作業ツリーには触れない。
#    本体経由で merge すると、本体で別セッションが作業しているあいだ統合できなくなる。
#    並行作業を成り立たせるのが worktree の目的なので、本体の状態に依存させない。
say ""
say "$default_branch へ push する"
if [[ "$ahead" == "0" ]]; then
  say "  push するものが無い"
elif (( dry_run )); then
  printf '  [dry-run] git push origin HEAD:%s\n' "$default_branch"
else
  # 直前に rebase 済みなので早送りになる。ここで弾かれるのは rebase 後に誰かが
  # push した場合で、そのときは再実行すれば新しい origin の上に載せ直される。
  git push --quiet origin "HEAD:$default_branch" || die "push が拒否された。再実行すると最新の origin の上に rebase し直す"
  say "  push した"
fi

# 本体のローカル既定ブランチを追随させておくと、次に本体で作業するときの pull が要らない。
# ただし本体が別セッションの作業中なら触らない。fetch は作業ツリーを書き換えないので常に行う。
if ! (( dry_run )); then
  git -C "$MAIN_REPO" fetch --quiet origin "$default_branch" 2>/dev/null || true
  main_branch_now="$(git -C "$MAIN_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  if [[ "$main_branch_now" == "$default_branch" && -z "$(git -C "$MAIN_REPO" status --porcelain)" ]]; then
    if git -C "$MAIN_REPO" merge --ff-only "origin/$default_branch" >/dev/null 2>&1; then
      say "  本体の $default_branch も最新にした"
    fi
  else
    say "  本体は作業中のため触っていない（次に本体で作業するとき git pull する）"
  fi
fi

# 4. 片付け。統合が済んでいるので worktree を消しても成果は失われない。
if (( keep )); then
  say ""
  say "--keep のため worktree は残す: ${WT_DIR/#$HOME/~}"
  say "後で片付けるときは本体で: git worktree remove ${WT_DIR/#$HOME/~} && git branch -d $branch"
elif (( PJ_WIN )); then
  say ""
  pids="$(pj_live_pids_under "$WT_DIR" | tr '\n' ' ')"
  if (( dry_run )); then
    printf '  [dry-run] この worktree の claude（pid: %s）が終わった後に、裏で worktree とブランチを片付ける\n' "${pids:-なし}"
  else
    # Claude Code のシェルが終わっても止まらないよう、PowerShell の Start-Process で切り離して起動する。
    # bash は -l で起動し、git などの PATH を揃える
    cleanup="$(dirname "${BASH_SOURCE[0]}")/wt-cleanup-later.sh"
    bash_exe="$(pj_win "$(command -v bash)")"
    arglist="-l \"$(pj_posix "$cleanup")\" \"$WT_DIR\" \"$MAIN_REPO\" \"$branch\" \"$default_branch\" $pids"
    powershell.exe -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '$bash_exe' -ArgumentList '${arglist//\'/\'\'}'" \
      || die "片付けを裏で起動できなかった。後で本体から: git worktree remove ${WT_DIR/#$HOME/~}"
    say "統合と push は済んだ。worktree とブランチは、この worktree の claude が終わった後に裏で片付ける"
    say "（Windows では使用中のフォルダを消せないため。記録は ~/.cache/pj/wt-cleanup.log）"
    say "claude を終える（/exit）か、窓や Orca のタブを閉じてよい"
  fi
else
  say ""
  say "worktree とブランチを片付ける"
  # 自分がいるディレクトリを消すことになるので、本体側から実行する
  run git -C "$MAIN_REPO" worktree remove "$WT_DIR"
  # `branch -d` は本体のローカル既定ブランチと比べるため、本体が作業中で追随を
  # 見送った場合は「not fully merged」で必ず落ちる（push は済んでいるのに）。
  # 統合の判定は push 先である origin 側で行い、そこに残りが無ければ削除する。
  if [[ -z "$(git -C "$MAIN_REPO" log --oneline "origin/$default_branch..$branch" 2>/dev/null)" ]]; then
    run git -C "$MAIN_REPO" branch -D "$branch"
  else
    say "  ブランチ $branch は origin/$default_branch に未統合のコミットを持つので残す"
  fi
  say ""
  say "完了。このウィンドウの cwd は消えたディレクトリを指しているので、閉じるか cd で移動する"
fi
