#!/usr/bin/env bash
# pj-open.sh — 指定プロジェクトを新規 iTerm2 ウィンドウで開き claude を起動する起動プリミティブ。
# Orca が動いていれば、iTerm2 ではなく Orca の作業場で開く（pj-orca.sh へ渡す。2026-11-04）。
#
# 今いる Claude セッションの中からでも、Bash 経由で「別プロジェクトの新規 claude セッション」を
# 兄弟ウィンドウとして起動するために使う（現在のウィンドウはそのまま残る）。
# /pj コマンド（~/.claude/commands/pj.md）・pj() の -w モードから呼ばれる共有プリミティブ。
# 学習ログ（choices.jsonl）への記録は 2026-07-26 に廃止した（L0 撤去に伴う）。
#
# pj() と同じく worktree へ振り替えて開く（2026-08-29 に追加）。それまでこの経路だけが
# 素の作業ツリーで起動しており、`/pj` と `pj -w` は分離されていなかった。同じリポジトリの
# 別サブフォルダで claude が2つ動くとインデックスを共有し、片方の `git add` にもう片方の
# ステージ済み変更が混ざる（2026-08-17・2026-08-26 に実際に起きた）。設計は
# 同じスキルの SKILL.md「worktree」。
#
# Usage:
#   pj-open.sh <relpath> [-c] [--query "<原文>"]
#     <relpath>   ~/Projects/ からの相対パス（例: utokyo/CRPC）
#     -c          新規ではなく直近セッションを継続（claude -c）。既定は新規 claude
#                 このときは worktree へ振り替えない（後述）
#     --query     語彙一致では当たらなかった原文。渡すと pj-learn.py が
#                 「この文でここを選んだ」として憶え、次の類似案件で当たるようになる。
#                 DIRECT（既に当たっている）経路からは渡さない — pj-go.sh は渡さず、
#                 /pj が CANDIDATES/CATALOG から判断して開くときだけ渡す。
#     --prompt-file <file>  起動と同時に送る最初の依頼文を書いたファイル（UTF-8）。claude の引数に
#                 そのまま渡し、開いた直後から作業が始まる。文面をシェルや AppleScript の文字列に
#                 埋め込まず、起動するシェルが $(cat) で読む（日本語・引用符の崩れを避ける）。
#                 pj-url（pj:// リンク）から使う。ファイルは起動後に消す
#     --request "<原文>"  pj に打った原文。行き先の名前だけでなく用件を含んでいれば、振り分けの1行を
#                 付けて最初の指示として渡す（判定は pj-router.py request）。--query の原文も同じ扱いにする。
#                 --prompt-file と併せて渡すと、依頼文の末尾に振り分けの1行だけを足す
#     --dry-run   ウィンドウを開かず学習もせず、cd 先として決まったパスだけを出して終わる。
#                 worktree への振り替えは実際に行われる（作られた worktree は自分で片付ける）
#
# 終了コード: 0=起動成功 / 1=引数不正 / 2=プロジェクト不在
set -euo pipefail

PROJECTS="$HOME/Projects"

relpath=""
continue_session=0
learn_query=""
dry_run=0
prompt_file=""
request=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--continue) continue_session=1; shift ;;
    -q|--query) learn_query="${2:-}"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --prompt-file) prompt_file="${2:-}"; shift 2 ;;
    --request) request="${2:-}"; shift 2 ;;
    -*) echo "pj-open.sh: 不明なオプション: $1" >&2; exit 1 ;;
    *) if [[ -z "$relpath" ]]; then relpath="$1"; shift; else echo "pj-open.sh: 引数が多すぎます: $1" >&2; exit 1; fi ;;
  esac
done

if [[ -z "$relpath" ]]; then
  echo "pj-open.sh: <relpath> が必要です（例: pj-open.sh utokyo/CRPC）" >&2
  exit 1
fi

# 先頭スラッシュ・末尾スラッシュを正規化
relpath="${relpath#/}"; relpath="${relpath%/}"
proj_dir="$PROJECTS/$relpath"

if [[ ! -d "$proj_dir" ]]; then
  echo "pj-open.sh: プロジェクトが存在しません: $proj_dir" >&2
  exit 2
fi

# 打った原文に用件があれば、開いた先への最初の指示にする（用件を開いた先で打ち直さないため）。
# 継続（-c）は前の会話に戻るだけなので渡さない
request="${request:-$learn_query}"
if [[ -n "$request" ]] && (( ! continue_session && ! dry_run )); then
  [[ -z "$prompt_file" || -f "$prompt_file" ]] || { echo "pj-open.sh: 依頼文のファイルが無い: $prompt_file" >&2; exit 1; }
  prompt_file="$(python3 "$(dirname "${BASH_SOURCE[0]}")/pj-router.py" request "$request" "$relpath" \
    ${prompt_file:+--body-file "$prompt_file"} --file)" || prompt_file=""
fi

# Orca が動いていれば、iTerm2 の窓ではなく Orca の作業場で開く（pj-orca.sh の冒頭）。
# 継続（-c）は Orca に同じ場所で再開する手段が無いので、従来どおり iTerm2 で開く。
# Orca で開けなかったときも iTerm2 へ戻る（起動を止めない）。
if (( ! continue_session && ! dry_run )); then
  pj_orca="$(dirname "${BASH_SOURCE[0]}")/pj-orca.sh"
  if bash "$pj_orca" --check \
     && bash "$pj_orca" "$relpath" ${prompt_file:+--prompt-file "$prompt_file"}; then
    if [[ -n "$learn_query" ]]; then
      python3 "$(dirname "${BASH_SOURCE[0]}")/pj-learn.py" record "$learn_query" "$relpath" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
fi

# 起動コマンド（新規ウィンドウは login shell → .zshrc で PATH 読込済み → claude 解決可）
launch_cmd="claude"
(( continue_session )) && launch_cmd="claude -c"
if [[ -n "$prompt_file" ]]; then
  [[ -f "$prompt_file" ]] || { echo "pj-open.sh: 依頼文のファイルが無い: $prompt_file" >&2; exit 1; }
  esc_pf=${prompt_file//\"/\\\"}
  # 読んだ直後に消す。claude は起動時に引数を受け取るので、消しても依頼文は失われない
  launch_cmd="${launch_cmd} \\\"\$(cat \\\"${esc_pf}\\\"; rm -f \\\"${esc_pf}\\\")\\\""
fi

# 実際に cd するディレクトリ。既定では専用 worktree の対応パスへ振り替える。
# 継続（-c）のときは振り替えない：worktree は開くたびに別ディレクトリになり、claude の
# セッション履歴はディレクトリ単位なので、振り替えると直近セッションを見失って
# 「継続」が成立しない。継続とは前回と同じ場所で再開することを指す。
launch_dir="$proj_dir"
if (( ! continue_session )); then
  # pj-worktree.sh は用意できなければ対象ディレクトリをそのまま返す（起動を止めない）
  launch_dir="$(bash "$(dirname "${BASH_SOURCE[0]}")/pj-worktree.sh" "$proj_dir" 2>/dev/null)" \
    || launch_dir="$proj_dir"
  [[ -n "$launch_dir" && -d "$launch_dir" ]] || launch_dir="$proj_dir"
fi

if (( dry_run )); then
  printf '%s\n' "$launch_dir"
  exit 0
fi

# AppleScript 文字列へ安全に埋め込むため二重引用符をエスケープ（~/Projects 配下は通常単純パス）
esc_dir=${launch_dir//\"/\\\"}

/usr/bin/osascript <<APPLESCRIPT
tell application "iTerm2"
  set w to (create window with default profile)
  tell current session of w
    -- 先頭の Ctrl-U は入力行に先に溜まった文字を消す（理由は mail-autopilot/resume-open.sh の冒頭）
    write text ((character id 21) & "cd \"${esc_dir}\" && exec ${launch_cmd}")
  end tell
  activate
end tell
APPLESCRIPT

# claude 起動後のフックで Typora/Finder が開き iTerm2 の前面を奪うことがあるため、
# 数秒間にわたり再前面化し続ける（起動直後だけでなく数秒後の横取りにも対応）
(
  for t in 1 2 3 5; do
    sleep "$t"
    /usr/bin/osascript -e 'tell application "iTerm2" to activate' >/dev/null 2>&1 || true
  done
) &
disown 2>/dev/null || true

# 学習は起動の後（記録に失敗しても起動は済んでいる状態にする）
if [[ -n "$learn_query" ]]; then
  python3 "$(dirname "${BASH_SOURCE[0]}")/pj-learn.py" record "$learn_query" "$relpath" >/dev/null 2>&1 || true
fi

echo "→ $relpath を新規ウィンドウで起動しました${prompt_file:+（依頼文を渡した）}" >&2
