#!/usr/bin/env bash
# 新しい依頼が入ったことを notify-chat.sh へ知らせるフック（UserPromptSubmit から呼ぶ）。
# やることは通知の予約を取り消すことだけ。まだ終わっていないので鳴らしてはいけない。
#
# これがあるおかげで、キューに積んだ依頼を消化している間は鳴らず、通知は全部片づいて
# 静かになってから1回だけ出る。
set -u
[ -f "$HOME/.config/claude-chat-notify/webhook.env" ] || exit 0   # 未設定の端末では何もしない

state_dir="${TMPDIR:-/tmp}/claude-chat-notify"
[ -d "$state_dir" ] || exit 0

# フックの標準入力（JSON）から session_id を取り出す。複数セッションを並行して
# 開いても互いの予約を壊さないため。取れなければ共通の名前に落とす。
sid="$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$sid" ] || sid="default"

rm -f "$state_dir/$sid.pending" 2>/dev/null || true
exit 0
