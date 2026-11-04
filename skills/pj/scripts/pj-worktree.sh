#!/usr/bin/env bash
# pj-worktree.sh — pj が開こうとするディレクトリを、必要なときだけ専用 worktree の
# 対応パスへ振り替える。併せて、空のまま残った worktree を回収する。
#
#   launch_dir=$(bash pj-worktree.sh <対象ディレクトリ>)
#
# 標準出力に「実際に cd すべきディレクトリ」を1行返す。何らかの理由で worktree を
# 用意できなければ対象ディレクトリをそのまま返すので、呼び出し側は戻り値を無条件に
# 使ってよい。**起動を止めないことを最優先にする**（pj は日常の入口で、ここで失敗すると
# 作業そのものが始められなくなる）。どちらのモードで開いたかは /dev/tty へ1行出す
# （呼び出し側は stderr を捨てるため stderr では届かない）。
#
# なぜ worktree を挟むか: git のインデックスはリポジトリ全体で共有された可変状態なので、
# 同じリポジトリの作業ツリーで claude を2つ動かすと、片方の `git add` にもう片方の
# ステージ済み変更が混ざる。2026-08-17 と 2026-08-26 に実際に起きた。
# 設計は同じスキルの SKILL.md「worktree」。
#
# 1つ目は本体で開く（2026-09-02 変更）。事故は本体で2セッションが重なったときにだけ
# 起きるので、本体が空いていれば本体で開き、埋まっているときだけ worktree へ回せば
# 性質は保たれる。それまでは1つ目から worktree にしていたが、実測すると26個中15個が
# コミットも変更も稼働セッションも無い空の残骸で、3個には統合されないまま見えなくなった
# 作業が残っていた（kenta は未統合6コミット）。生成を半分に減らし、深いパスと統合手順を
# 単独セッションから外す。
#
# 速度が要件（pj は体感で3秒以内に立ち上がること）。そのため origin への fetch はしない。
# 判定・回収はローカルの参照だけで行い、origin との同期は統合時（wt-finish.sh）に行う。

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/pj-platform.sh"

target="$(pj_posix "${1:-}")"
[[ -n "$target" && -d "$target" ]] || { printf '%s\n' "$target"; exit 0; }

# 以降どこで失敗しても、対象ディレクトリを返して起動は続けさせる
fallback() { printf '%s\n' "$target"; exit 0; }

# モード表示。呼び出し側3箇所とも stderr を /dev/null へ捨てるので tty へ直接書く。
# リダイレクト自体の失敗は bash が出すので、複合コマンドごと stderr を捨てる
# （制御端末が無い環境＝フック・CI・Bash ツール経由では黙って何も出さない）。
note() { { printf 'pj: %s\n' "$*" > /dev/tty; } 2>/dev/null; return 0; }

repo="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null)" || fallback
repo="$(pj_posix "$repo")"
[[ -n "$repo" ]] || fallback

# 既に worktree の中なら入れ子にしない。そのまま使う。
# 両方を絶対パスへ正規化してから比べる。--git-dir は絶対パスを返すのに
# --git-common-dir は cwd からの相対パス（`../.git` 等）を返すことがあり、
# 素の文字列比較だと本体を worktree と誤判定する（2026-08-26 に踏んだ）。
git_dir="$(git -C "$target" rev-parse --absolute-git-dir 2>/dev/null)" || fallback
git_dir="$(pj_posix "$git_dir")"
common_dir="$(cd "$target" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd)" || fallback
[[ -n "$git_dir" && -n "$common_dir" ]] || fallback
[[ "$git_dir" == "$common_dir" ]] || fallback

wt_root="$repo/.claude/worktrees"

# 本体の .claude/worktrees/ 配下を指している場合も対象外（上の判定で拾えない経路の保険）
[[ "$target" != "$wt_root"* ]] || fallback

# --- 稼働中の claude セッションの cwd 一覧 -----------------------------------
#
# 取り方（Mac は ps と lsof、Windows は ~/.claude/sessions と tasklist）は pj-platform.sh。
live_cwds() { pj_live_cwds; }

# パス $1 が $2 自身かその配下か（"$repo-other" を "$repo" の配下と誤判定しない）
under() { [[ "$1" == "$2" || "$1" == "$2"/* ]]; }

cwds="$(live_cwds)"

# --- 空のまま残った worktree を回収する -------------------------------------
#
# 異常終了すると worktree は残る。放置すると名前が root-2, root-3, root-4 と伸び続け、
# 中身のある worktree が残骸に埋もれて見えなくなる。稼働セッションが無く・既定ブランチ
# より進んだコミットが無く・未コミットも無いものだけを消す。中身があるものは消さずに残す
# （統合されていない作業を落とさないため。`git worktree remove` も `git branch -d` も
# --force を付けない）。
#
# 既定ブランチは origin/HEAD から取るが、比較はローカル参照で行う。fetch しないため。
# ローカルが origin より遅れている場合、worktree 側が進んで見えて「残す」に倒れる＝安全側。
reclaim() {
  local default_branch wt ahead reclaimed=0 live
  default_branch="$(git -C "$repo" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
  default_branch="${default_branch#origin/}"
  [[ -n "$default_branch" ]] || default_branch=main
  git -C "$repo" show-ref --verify --quiet "refs/heads/$default_branch" || return 0

  while read -r wt; do
    wt="$(pj_posix "$wt")"   # git は Windows で C:/… を返す
    [[ -n "$wt" && -d "$wt" ]] || continue
    # pj が作った置き場（.claude/worktrees/）のものだけを回収する。Orca などほかの道具が作った
    # worktree は、その道具が持ち主で、エージェントを止めているだけの空の作業場もある。
    # ここで消すと持ち主の画面から作業場が消える（2026-11-03、Orca の導入時に追加）。
    [[ "$wt" == "$wt_root/"* ]] || continue
    # 稼働セッションがその中に居るなら触らない
    live=0
    while read -r c; do
      [[ -n "$c" ]] || continue
      under "$c" "$wt" && { live=1; break; }
    done <<< "$cwds"
    (( live )) && continue

    # 作りたてには触らない。claude は起動に数秒かかり、その間 worktree は
    # 「セッションが居ない・空」に見える。次の起動がその窓で回収すると、
    # 立ち上がりかけのセッションの足元を消すことになる。
    [[ -n "$(find "$wt" -maxdepth 0 -mmin -5 2>/dev/null)" ]] && continue

    ahead="$(git -C "$wt" rev-list --count "$default_branch..HEAD" 2>/dev/null)" || continue
    [[ "$ahead" == "0" ]] || continue
    [[ -z "$(git -C "$wt" status --porcelain 2>/dev/null)" ]] || continue

    local br
    br="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    git -C "$repo" worktree remove "$wt" >/dev/null 2>&1 || continue
    [[ -n "$br" && "$br" != HEAD ]] && git -C "$repo" branch -d "$br" >/dev/null 2>&1
    reclaimed=$((reclaimed + 1))
  done < <(git -C "$repo" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | tail -n +2)

  (( reclaimed > 0 )) && note "空の worktree を ${reclaimed} 個回収した"
  return 0
}
reclaim

# --- 本体が空いていれば本体で開く -------------------------------------------
#
# 「空いている」＝本体の作業ツリーの中に稼働中の claude が居ない。worktree 側で
# 動いているセッションはインデックスを共有しないので、居ても本体は使ってよい。
main_busy=0
while read -r c; do
  [[ -n "$c" ]] || continue
  under "$c" "$repo" || continue
  [[ "$c" == "$wt_root"/* ]] && continue   # worktree 側は本体の占有ではない
  main_busy=1; break
done <<< "$cwds"

if (( ! main_busy )); then
  note "本体で開く（このリポジトリに稼働中のセッションなし）"
  printf '%s\n' "$target"
  exit 0
fi

# --- ここから先は本体が埋まっている。worktree を用意する --------------------

# リポジトリルートからの相対パス。worktree 名とその中での位置の両方に使う。
rel="${target#$repo}"
rel="${rel#/}"

if [[ -z "$rel" ]]; then
  base_name="root"
else
  base_name="${rel//\//-}"
fi

# 既存を再利用せず別の worktree にする。再利用すると2つのセッションが同じ
# インデックスを共有し、避けたかった状態に戻る。上の回収で空きが出ていれば
# 番号は伸びずに元の名前へ戻る。
name="$base_name"
n=2
while [[ -e "$wt_root/$name" ]] || git -C "$repo" show-ref --verify --quiet "refs/heads/wt/$name"; do
  name="$base_name-$n"
  n=$((n + 1))
  (( n > 50 )) && fallback   # 異常に溜まっている。worktree を挟まず素で開く
done

# 置き場を無視させる。.gitignore ではなく .git/info/exclude に書くのは、
# 他の人へ配られる設定を勝手に書き換えないため（リポジトリローカル・コミット不要）。
exclude_file="$(cd "$common_dir" 2>/dev/null && pwd)/info/exclude"
if [[ -f "$exclude_file" ]] && ! grep -qF '.claude/worktrees/' "$exclude_file" 2>/dev/null; then
  printf '\n# pj が並行セッション用に作る worktree の置き場（pj-worktree.sh が自動追記）\n.claude/worktrees/\n' >> "$exclude_file" 2>/dev/null || true
fi

mkdir -p "$wt_root" 2>/dev/null || fallback

# fetch しない。速度が要件で、origin との同期は wt-finish.sh が統合時に行う。
git -C "$repo" worktree add --quiet -b "wt/$name" "$wt_root/$name" HEAD 2>/dev/null || fallback

launch_dir="$wt_root/$name"
[[ -n "$rel" ]] && launch_dir="$launch_dir/$rel"

# 対象サブフォルダが新しい worktree に存在しない（未コミットのディレクトリ等）場合は
# worktree のルートで開く。存在しないパスへ cd すると起動そのものが失敗する。
[[ -d "$launch_dir" ]] || launch_dir="$wt_root/$name"

note "worktree で開く（本体で稼働中のセッションあり）: .claude/worktrees/${name}"
printf '%s\n' "$launch_dir"
