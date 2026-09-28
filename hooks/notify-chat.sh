#!/usr/bin/env bash
# 依頼された処理が全部終わったことを Google Chat のスペースへ知らせる（Stop フックから呼ぶ）。
#
# 既定では鳴らない。「終わったら通知して」と頼まれたときだけ、Claude が
# notify-chat-arm.sh を実行して有効にする。毎回鳴らすと通知そのものが読まれなくなるため。
#
# 送るのは端末名・プロジェクト名・時刻と、有効にしたときに書いた1行の要約だけ。
# それ以上は載せない——スペースは他の人と共有されうるうえ、作業内容には人事・個人情報・
# 未公開の検討が混ざりうるので、本文の組み立てをスクリプトの外へ広げない。
#
# 仕組みと導入の正本は claude-toolkit の hooks/README.md。
#
# Webhook URL は追跡ファイルに置かず ~/.config/claude-chat-notify/webhook.env から読む。
# 未設定の端末では黙って何もしない（フックが壊れて作業を止めないため）。
#
# 動作確認：  ~/.claude/hooks/notify-chat.sh --test
set -u

conf="$HOME/.config/claude-chat-notify/webhook.env"
[ -f "$conf" ] || exit 0
# shellcheck source=/dev/null
. "$conf" 2>/dev/null || exit 0
[ -n "${CHAT_WEBHOOK_URL:-}" ] || exit 0

mode="${1:-}"

# テンプレートを置いただけで URL をまだ入れていない状態。毎回むだに POST しない。
case "$CHAT_WEBHOOK_URL" in
  *XXXX*)
    [ "$mode" = "--test" ] && echo "webhook.env の CHAT_WEBHOOK_URL がテンプレートのままです。実際の URL を入れてください。" >&2
    exit 0 ;;
esac

state_dir="${TMPDIR:-/tmp}/claude-chat-notify"
armed="$state_dir/armed"

send_now() {
  label="$1"
  summary="$2"
  fmt="${3:-}"
  host="$(hostname 2>/dev/null || echo 'unknown-host')"
  host="$(printf '%s' "$host" | tr -cd 'A-Za-z0-9._-')"
  proj="$(basename "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || echo unknown)"
  proj="$(printf '%s' "$proj" | tr -cd 'A-Za-z0-9._-')"

  # 要約に JSON を壊す文字が混ざっていたら落とす。書くのは Claude だが、
  # 壊れた本文は送信が成功したように見えて中身だけが失われるので、ここでも止める。
  summary="$(printf '%s' "$summary" | tr -d '"' | tr -d '\\' | tr -d '\r')"
  sumline=""
  [ -n "$summary" ] && sumline="\n$summary"

  body="{\"text\":\"✅ Claude ${label} — ${host} / ${proj} $(date '+%H:%M')${sumline}\"}"

  # 本文は引数ではなくファイルで渡す。Git Bash からネイティブの curl.exe へ引数を渡すと
  # 非 ASCII が ANSI コードページを経て壊れる（2026-08-30 実測：日本語と絵文字が
  # 化けたまま Chat に届いた）。ファイル経由なら同じ本文がそのまま届く。
  tmpf="$state_dir/body-$$.json"
  mkdir -p "$state_dir" 2>/dev/null || return 0
  printf '%s' "$body" > "$tmpf" 2>/dev/null || return 0

  curl -sS -m 10 -X POST \
    -H 'Content-Type: application/json; charset=UTF-8' \
    --data-binary "@$tmpf" \
    "$CHAT_WEBHOOK_URL" -o /dev/null -w "$fmt" 2>/dev/null || true
  rm -f "$tmpf" 2>/dev/null || true
}

case "$mode" in
  --test)
    # 有効化も予約も挟まずその場で送り、結果を見せる。
    send_now "通知テスト" "配線の確認" "HTTP %{http_code}"
    echo ""
    exit 0 ;;
  --deferred)
    # 予約の実行役。自分を裏で呼び直した姿で、静かな時間が続いたときだけ送る。
    # 途中で新しい依頼が入れば notify-chat-start.sh が予約を消すので、ここは何もせず終わる。
    sid="${2:-default}"
    token="${3:-}"
    sleep "${CHAT_NOTIFY_QUIET_SECONDS:-20}"
    pending="$state_dir/$sid.pending"
    [ "$(cat "$pending" 2>/dev/null)" = "$token" ] || exit 0   # 別の応答が予約を取り直した
    rm -f "$pending" 2>/dev/null || true
    [ -f "$armed" ] || exit 0                                  # 待っている間に取り消された
    keep="$(sed -n 1p "$armed" 2>/dev/null)"
    summary="$(sed -n 2p "$armed" 2>/dev/null)"
    [ "$keep" = "session" ] || rm -f "$armed" 2>/dev/null || true   # once は1回で降ろす
    send_now "完了" "$summary" ""
    exit 0 ;;
esac

# ここから Stop フック本体。
[ -f "$armed" ] || exit 0    # 頼まれていないので鳴らさない（既定はこちら）

# 標準入力の JSON は一度しか読めないので変数に受ける。
payload="$(cat)"

# サブエージェントの完了（SubagentStop）では鳴らさない。知りたいのは全部終わったこと
# だけで、途中の区切りではない。イベント名が取れない版では従来どおり通す。
ev="$(printf '%s' "$payload" | sed -n 's/.*"hook_event_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
case "$ev" in
  ''|Stop) : ;;
  *) exit 0 ;;
esac

sid="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$sid" ] || sid="default"

# 送らずに予約だけ立てる。静かな時間（既定20秒）のあいだに次の依頼が入らなければ、
# 裏で待っている自分自身が送る。キューに積まれた依頼を消化している間は、
# そのたびに予約が取り直されるので鳴らない。
mkdir -p "$state_dir" 2>/dev/null || exit 0
token="$(date +%s%N 2>/dev/null || date +%s)-$$"
printf '%s' "$token" > "$state_dir/$sid.pending" 2>/dev/null || exit 0
nohup "$0" --deferred "$sid" "$token" >/dev/null 2>&1 &
exit 0
