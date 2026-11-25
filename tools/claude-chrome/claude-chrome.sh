#!/usr/bin/env bash
# AI（Claude Code）が操作する Chrome・Edge を、人が使うブラウザと分けて起動・操作する（macOS）。
# プロファイルは ~/.config/claude-chrome/ に保存して使い回す（拡張と Cookie を残す。使い捨てのプロファイルを作らない）。
# 設計・プロファイルの設定・アイコンの正本は同じフォルダの README.md。install.py が ~/.local/bin/claude-chrome に置く。
#
# 使い方（--<呼び名> は ~/.config/claude-chrome/profiles.conf の1列目。省くと1行目のプロファイル）:
#   claude-chrome [--<呼び名>] start          起動（起動済みなら、空タブの補充とアイコンの掛け直しだけ）
#   claude-chrome [--<呼び名>] status         生存確認（CDP に応答するか）
#   claude-chrome [--<呼び名>] open <URL>     URL を開いて前面に出す（人がログインする用。未起動なら起動）
#   claude-chrome [--<呼び名>] tab <URL>      前面へ出さずにタブを開き、targetId を出力する（スクリプト用。未起動なら起動）
#   claude-chrome [--<呼び名>] tabs           開いているタブの URL を一覧
#   claude-chrome [--<呼び名>] release [--match <正規表現>] [targetId...]
#                                             自分が開いたタブを閉じ、中身のあるタブが1枚も残らなければ Chrome も終了する。
#                                             無人のスクリプトは終わりに必ずこれを通す（finally / trap）。規則は release.mjs 冒頭
#   claude-chrome [--<呼び名>] stop           終了（残っているタブを問わず落とす。プロファイルは消さない）
#   claude-chrome [--<呼び名>] port           CDP のポートだけを出力する（スクリプトが接続先を設定から引く用）
#   claude-chrome profiles                    設定されたプロファイルと起動の有無
set -euo pipefail

HERE="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
BASE="$HOME/.config/claude-chrome"
CONF="${CLAUDE_CHROME_CONF:-$BASE/profiles.conf}"

# profiles.conf の行（呼び名 フォルダ ポート 帯の文字 帯の色 [ブラウザ]）。# で始まる行と空行は読まない
rows() { [ -f "$CONF" ] && grep -v -E '^[[:space:]]*(#|$)' "$CONF" || true; }

ROW=""
if [[ "${1:-}" == --?* ]]; then
  NAME="${1#--}"; shift
  ROW="$(rows | awk -v n="$NAME" '$1 == n' | head -1)"
  [ -n "$ROW" ] || { echo "$CONF に「${NAME}」がありません（claude-chrome profiles で一覧）" >&2; exit 2; }
  unset CLAUDE_CHROME_PROFILE CLAUDE_CHROME_PORT  # 名指しが環境変数より勝つ
elif [ -n "${CLAUDE_CHROME_PROFILE:-}" ]; then
  ROW="$(rows | awk -v d="$(basename "$CLAUDE_CHROME_PROFILE")" '$2 == d' | head -1)"
else
  ROW="$(rows | head -1)"
fi
read -r _ DIR PORT_C LABEL_C COLOR_C BROWSER_C <<<"$ROW" || true
PROFILE="${CLAUDE_CHROME_PROFILE:-$BASE/${DIR:-profile}}"
PORT="${CLAUDE_CHROME_PORT:-${PORT_C:-9222}}"
LABEL="${CLAUDE_CHROME_LABEL:-${LABEL_C:-$(basename "$PROFILE")}}"
COLOR="${CLAUDE_CHROME_COLOR:-${COLOR_C:-#5F6368}}"
case "${CLAUDE_CHROME_BROWSER:-${BROWSER_C:-chrome}}" in
  chrome) APP="Google Chrome" ;;
  edge)   APP="Microsoft Edge" ;;
  *) echo "ブラウザは chrome か edge: ${BROWSER_C}" >&2; exit 2 ;;
esac
APP_PATH="/Applications/${APP}.app"

cdp_up() { curl -s -m 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; }

page_count() {
  curl -s -m 2 "http://127.0.0.1:${PORT}/json/list" 2>/dev/null |
    python3 -c 'import json,sys
try: print(sum(1 for t in json.load(sys.stdin) if t["type"] == "page"))
except Exception: print(0)'
}

# タブが1枚も無い状態を作らない。
# macOS では全ウィンドウを閉じても Chrome のプロセスは残り、CDP も応答し続ける。その状態は browser context が
# 1つも無いため、Playwright の connectOverCDP が `Browser context management is not supported` で失敗する
# （2026-08-11 実測）。空タブを1枚立てれば通る。空タブは前面へ出さずに開く（/json/new は Chrome を前面へ出す）
ensure_tab() {
  cdp_up || return 1
  [[ "$(page_count)" -gt 0 ]] && return 0
  node "$HERE/new-tab.mjs" "$PORT" about:blank >/dev/null 2>&1
  [[ "$(page_count)" -gt 0 ]]
}

# 起動した Chrome は、open -g で起動しても自分で前面へ出てくる（2026-11-11 実測。最初の約5秒に何度か取る）。
# 別作業の手元を奪わないよう、起動の前に前面だったアプリを覚え、Chrome が前面を取ったら戻す。
# 人が自分で別のアプリへ移ったときは、Chrome ではないので触らない。
front_pid() {
  osascript -e 'tell application "System Events" to return unix id of first application process whose frontmost is true' 2>/dev/null || true
}
PREV_FRONT=""
save_front() { PREV_FRONT="$(front_pid)"; }
give_back_front() {  # give_back_front <回数>   0.5 秒おきに見て、Chrome が前面なら元へ戻す
  [ -n "$PREV_FRONT" ] || return 0
  local now name i
  for i in $(seq "$1"); do
    now="$(front_pid)"
    if [ -n "$now" ] && [ "$now" != "$PREV_FRONT" ]; then
      name="$(osascript -e "tell application \"System Events\" to return name of first application process whose unix id is $now" 2>/dev/null || true)"
      [ "$name" = "$APP" ] &&
        osascript -e "tell application \"System Events\" to set frontmost of (first application process whose unix id is $PREV_FRONT) to true" >/dev/null 2>&1
    fi
    sleep 0.5
  done
}

# Dock と Cmd+Tab のアイコンを「AI の印＋プロファイルの帯」に差し替える。プロセスが終わると元に戻るので、start のたびに掛ける。
# 失敗しても起動は止めない（見た目だけの機能のため）。
apply_icon() {
  local png="$BASE/icons/$(printf '%s' "$APP $LABEL $COLOR" | tr -cd 'A-Za-z0-9._-').png"
  if [ ! -s "$png" ]; then
    mkdir -p "$(dirname "$png")"
    osascript -l JavaScript "$HERE/icon/make-icon.js" "$APP_PATH" "$png" "$LABEL" "$COLOR" >/dev/null 2>&1
  fi
  [ -s "$png" ] && node "$HERE/icon/set-dock-tile.mjs" "$PORT" "$png" >/dev/null 2>&1 ||
    echo "アイコンを差し替えられませんでした（起動には影響なし）" >&2
}

start() {
  if cdp_up; then ensure_tab; apply_icon; return 0; fi
  [ -d "$APP_PATH" ] || { echo "$APP_PATH がありません" >&2; return 1; }
  mkdir -p "$PROFILE"
  [ "${FOREGROUND:-0}" = 1 ] || save_front  # open（人がログインする）のときは前面に出したままにする
  # 実体を直接起動せず open で起動する。ssh のセッションから実体を起動すると GUI のセッションの外の Chrome になり、
  # キーチェーンにある Cookie の鍵を読めない。open なら launchd の子として起動する（2026-11-11 確認）。
  # -g は前面に出さない指定だが、Chrome は自分で前に出る（give_back_front が戻す）
  local bg=-g; [ "${FOREGROUND:-0}" = 1 ] && bg=
  open $bg -n -a "$APP" --args \
    --user-data-dir="$PROFILE" \
    --remote-debugging-port="$PORT" \
    --no-first-run --no-default-browser-check \
    "$@" >/dev/null 2>&1
  for _ in $(seq 30); do
    sleep 0.5
    give_back_front 1
    if cdp_up; then ensure_tab; apply_icon; give_back_front 10; return 0; fi
  done
  echo "起動できませんでした: ${PROFILE}（同じフォルダの Chrome が別のポートで動いていれば、stop してからやり直す）" >&2
  return 1
}

case "${1:-status}" in
  start)
    start
    echo "起動しています: CDP http://127.0.0.1:${PORT} / profile ${PROFILE}"
    ;;
  status)
    if cdp_up; then
      curl -s "http://127.0.0.1:${PORT}/json/version" | sed -n 's/.*"Browser": "\(.*\)",/\1/p'
      echo "CDP http://127.0.0.1:${PORT} / profile ${PROFILE}"
      echo "タブ $(page_count) 枚（0 枚だと connectOverCDP が失敗する。start で解消）"
    else
      echo "起動していません（claude-chrome start）"
      exit 1
    fi
    ;;
  open)
    url="${2:?URL を指定してください}"
    if cdp_up; then
      # 同じ user-data-dir で起動し直すと、走っているインスタンスにタブが渡る
      open -na "$APP" --args --user-data-dir="$PROFILE" "$url"
    else
      FOREGROUND=1 start "$url"
    fi
    ;;
  tab)
    url="${2:-about:blank}"
    cdp_up || { start >/dev/null; }
    node "$HERE/new-tab.mjs" "$PORT" "$url"
    ;;
  release)
    shift
    node "$HERE/release.mjs" "$PORT" "$@"
    ;;
  tabs)
    cdp_up || { echo "起動していません" >&2; exit 1; }
    curl -s "http://127.0.0.1:${PORT}/json/list" |
      python3 -c 'import json,sys
for t in json.load(sys.stdin):
    if t["type"] == "page":
        print(t["url"])'
    ;;
  port)
    echo "$PORT"
    ;;
  stop)
    # 末尾を区切る。区切らないと profile の stop が profile-xxx の Chrome まで落とす
    pkill -f -- "--user-data-dir=${PROFILE}( |\$)" || true
    echo "終了しました"
    ;;
  profiles)
    [ -f "$CONF" ] || { echo "$CONF がありません。既定（$BASE/profile・CDP 9222）だけを使います"; exit 0; }
    first=1
    rows | while read -r n d p l c b; do
      st="停止"; curl -s -m 1 "http://127.0.0.1:${p}/json/version" >/dev/null 2>&1 && st="起動中"
      echo "--$n  ${b:-chrome}  $BASE/$d  CDP $p  帯「${l}」$c  $st$([ $first = 1 ] && echo '  （既定）')"
      first=0
    done
    ;;
  *)
    sed -n '2,18p' "$(realpath "$0")"
    exit 1
    ;;
esac
