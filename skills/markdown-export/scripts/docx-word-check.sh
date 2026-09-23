#!/usr/bin/env bash
# docx を Microsoft Word（macOS）で開き、Word が数えたページ数を表示して PDF を書き出す。
#
#   scripts/docx-word-check.sh <input.docx> [output.pdf]
#
# docx の見た目・ページ数の確認はこれで行う。Quick Look（qlmanage）は文書の書体を使わず
# 代替の明朝体で描き、文書グリッドも無視するため、行の高さ・折り返し・ページ数が Word と
# 一致しない（2026-09-23 実測：Quick Look で1枚に見えた版が Word では2ページ）。
#
# Word はサンドボックス化されているので、コンテナ内の tmp に写しを置いて開く（ファイル
# アクセスの許可ダイアログを出さないため）。元の docx には触れない。Word が一瞬前面に出る。
set -euo pipefail
in="${1:?usage: docx-word-check.sh <input.docx> [output.pdf]}"
out="${2:-${in%.docx}.pdf}"
[[ -d "/Applications/Microsoft Word.app" ]] || { echo "Microsoft Word が見つかりません" >&2; exit 1; }
work="$HOME/Library/Containers/com.microsoft.Word/Data/tmp/docx-word-check.$$"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT
cp "$in" "$work/in.docx"
pages=$(osascript - "$work/in.docx" "$work/out.pdf" <<'APPLESCRIPT'
on run argv
	set inPath to item 1 of argv
	set outPath to item 2 of argv
	tell application "Microsoft Word"
		open (POSIX file inPath)
		set theDoc to active document
		set n to (get compute statistics theDoc statistic statistic pages)
		save as theDoc file name outPath file format format PDF
		close theDoc saving no
	end tell
	return n
end run
APPLESCRIPT
)
cp "$work/out.pdf" "$out"
echo "pages=$pages  pdf=$out"
