#!/usr/bin/env bash
# 「終わったら通知して」と言われたときに Claude が実行する。次に処理が全部片づいた
# 時点で Google Chat へ1回だけ通知が出るようにする。既定では通知は出ない。
#
# 要約は標準入力から1行で受け取る。引数で渡さないのは、Bash ツール経由で非 ASCII が
# 壊れることがあるため（~/.claude/instructions/tools.md「シェルスクリプトの生成」）。
#
#   ~/.claude/hooks/notify-chat-arm.sh once <<'EOF'
#   SDTM の適合性検証を回す
#   EOF
#
# once は次の1回だけ、session はそのセッションのあいだ毎回。取り消しは --off。
set -u

conf="$HOME/.config/claude-chat-notify/webhook.env"
if [ ! -f "$conf" ]; then
  echo "この端末では Chat 通知が設定されていない（$conf が無い）" >&2
  exit 0
fi

state_dir="${TMPDIR:-/tmp}/claude-chat-notify"
armed="$state_dir/armed"
mkdir -p "$state_dir" 2>/dev/null || exit 1

case "${1:-once}" in
  --off)
    rm -f "$armed" 2>/dev/null || true
    echo "通知を取り消しました"
    exit 0 ;;
  once|session)
    keep="${1:-once}" ;;
  *)
    echo "使い方: notify-chat-arm.sh [once|session|--off]  （要約は標準入力から1行）" >&2
    exit 1 ;;
esac

summary="$(head -1 | tr -d '"' | tr -d '\\' | tr -d '\r')"
{ printf "%s\n" "$keep"; printf "%s\n" "$summary"; } > "$armed" || exit 1
echo "次の完了で通知します（${keep}）"
exit 0
