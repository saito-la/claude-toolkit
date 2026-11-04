#!/usr/bin/env bash
# orca-install-user.sh — 管理者権限と Homebrew の無い Mac（組織の管理下の端末など）に Orca を入れる。
#
#   bash <このフォルダ>/orca-install-user.sh [--version 1.4.247]
#
# brew の cask（README.md「入れ方」）が使えない端末向け。
# 何度実行しても同じ状態になる（入っている版が同じなら取り直さない）。
#
# 1. GitHub の配布物（zip）を取り、署名（Developer ID の 6CX3WHS9HZ）と公証を確かめて
#    ~/Applications/Orca.app に置く。管理者権限は要らない
# 2. `orca` CLI を ~/.local/bin に張る
# 3. GUI のアプリへ環境変数を渡す LaunchAgent（com.saito.orca-env）を入れ、今の GUI セッションにも渡す
#    - ORCA_TELEMETRY_DISABLED=1：利用統計を送らない
#    - NODE_EXTRA_CA_CERTS：TLS を傍受する網では、Electron のメイン側（Node）の通信が
#      証明書で落ちるため、CA の PEM を渡す。シェルに NODE_EXTRA_CA_CERTS が既にあればそれを使い、
#      無くて傍受（Cisco Umbrella）を検出したら tls-ca-bundle.sh で作る
# 4. Orca を起動し、つながったら ~/Projects のリポジトリを登録する（orca-register-projects.py）
#
# 画面での設定（権限・一覧の表示）は最後に案内を出す。中身の正本は README.md「画面での設定」。
set -euo pipefail

VERSION="1.4.247"
[[ "${1:-}" == "--version" ]] && VERSION="${2:?版を指定する}"
TEAM_ID="6CX3WHS9HZ"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/Applications/Orca.app"
BIN="$HOME/.local/bin"
CA="${NODE_EXTRA_CA_CERTS:-$HOME/.local/etc/hospnet-ca.pem}"
LABEL="com.saito.orca-env"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '==> %s\n' "$*"; }
die() { printf 'orca-install-user: %s\n' "$*" >&2; exit 1; }

# ---- 1. アプリ ----
have="$(defaults read "$APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || true)"
if [[ "$have" == "$VERSION" ]]; then
  say "Orca $VERSION は入っている（${APP}）"
else
  case "$(uname -m)" in
    arm64) asset="Orca-$VERSION-arm64-mac.zip" ;;
    x86_64) asset="Orca-$VERSION-mac.zip" ;;
    *) die "対応していない CPU: $(uname -m)" ;;
  esac
  pgrep -x Orca >/dev/null && die "Orca が起動している。終了してから実行する"
  tmp="$(mktemp -d -t orca-install)"
  say "取得：$asset"
  # curl はシステムキーチェーンを見るので、TLS 傍受の網でも通る
  curl -fL --retry 2 -o "$tmp/$asset" "https://github.com/stablyai/orca/releases/download/v$VERSION/$asset"
  ditto -x -k "$tmp/$asset" "$tmp/x"
  [[ -d "$tmp/x/Orca.app" ]] || die "zip の中に Orca.app が無い"
  codesign --verify --deep --strict "$tmp/x/Orca.app" || die "署名が壊れている"
  team="$(codesign -dv "$tmp/x/Orca.app" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  [[ "$team" == "$TEAM_ID" ]] || die "署名の Team ID が違う（${team}）"
  spctl -a -t exec "$tmp/x/Orca.app" 2>/dev/null || say "注意：公証を確かめられなかった（spctl。網が塞がれていると起きる）。署名は確かめ済み"
  mkdir -p "$HOME/Applications"
  [[ -d "$APP" ]] && mv "$APP" "$tmp/Orca.old.app"   # 古い版は一時置き場へ（OS が片付ける）
  mv "$tmp/x/Orca.app" "$APP"
  say "置いた：${APP}（$have → ${VERSION}）"
fi

# ---- 2. CLI ----
mkdir -p "$BIN"
ln -sfn "$APP/Contents/Resources/bin/orca" "$BIN/orca"
say "CLI：$BIN/orca"

# ---- 3. GUI へ渡す環境変数 ----
envs=(ORCA_TELEMETRY_DISABLED 1)
if [[ -n "${NODE_EXTRA_CA_CERTS:-}" && -s "$CA" ]]; then
  envs+=(NODE_EXTRA_CA_CERTS "$CA")
elif security find-certificate -a -c "Cisco Umbrella" /Library/Keychains/System.keychain >/dev/null 2>&1; then
  [[ -s "$CA" ]] || bash "$SCRIPTS/tls-ca-bundle.sh"
  [[ -s "$CA" ]] && envs+=(NODE_EXTRA_CA_CERTS "$CA")
fi
cmd=""
for ((i = 0; i < ${#envs[@]}; i += 2)); do
  launchctl setenv "${envs[i]}" "${envs[i+1]}"
  cmd+="launchctl setenv ${envs[i]} '${envs[i+1]}'; "
done
mkdir -p "$(dirname "$PLIST")"
cat >"$PLIST.new" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>-c</string><string>$cmd</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
if ! cmp -s "$PLIST.new" "$PLIST"; then
  mv "$PLIST.new" "$PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  rm -f "$PLIST.new"
fi
say "GUI の環境変数：$(printf '%s ' "${envs[@]}")（${LABEL}）"

# ---- 4. 起動と登録 ----
open "$APP"
say "Orca の起動を待つ"
for _ in $(seq 1 60); do
  "$BIN/orca" status --json 2>/dev/null | grep -q '"reachable": true' && break
  sleep 2
done
"$BIN/orca" status --json 2>/dev/null | grep -q '"reachable": true' \
  || die "Orca につながらない。画面で初回の確認を済ませてから、もう一度実行する"
python3 "$SCRIPTS/orca-register-projects.py"

cat <<'EOF'

==> 画面で行うこと（正本は README.md「画面での設定」）
  1. Settings → Agents → Permissions を「Each agent's own settings」にする（Full access のままにしない）
  2. Settings → Git & Source Control で、作業場を作るときに元のブランチを最新にする設定をオンにする
  3. 左の一覧の Workspace options：Group by を None、Sort by を Agent Activity、Hide sleeping をオン、Except default branch をオフ
EOF
