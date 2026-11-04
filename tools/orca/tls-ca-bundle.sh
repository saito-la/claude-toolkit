#!/usr/bin/env bash
# tls-ca-bundle.sh
#
# TLS を傍受する網（例：Cisco Umbrella）では、全 HTTPS の証明書が差し替えられる。
# macOS のシステムキーチェーンには傍受装置の root CA が MDM 経由で入っているため
# curl・git・Safari は通るが、**Node は自前の Mozilla CA バンドルしか見ない**ので
# 全通信が UNABLE_TO_GET_ISSUER_CERT_LOCALLY で落ちる（npm・MCP サーバー・clasp・playwright）。
#
# 対処はシステムキーチェーンを PEM 化して NODE_EXTRA_CA_CERTS に食わせること。
# 本スクリプトはその PEM を生成する。~/.zshrc の export と、MCP 登録時の
# `-e NODE_EXTRA_CA_CERTS=...` が参照する。
#
# 傍受の有無は次で確認できる（issuer が Cisco なら傍受下）:
#   echo | openssl s_client -connect registry.npmjs.org:443 -servername registry.npmjs.org 2>/dev/null \
#     | openssl x509 -noout -issuer
#
# 使い方: bash tls-ca-bundle.sh   （書き出し先は既定で ~/.local/etc/hospnet-ca.pem。旧名を保つ）
set -euo pipefail

OUT="$HOME/.local/etc/hospnet-ca.pem"
mkdir -p "$(dirname "$OUT")"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# System.keychain に MDM が入れた企業CA、SystemRootCertificates に標準ルート。
# NODE_EXTRA_CA_CERTS は Node 内蔵バンドルに「足す」ので両方入れて構わない。
security find-certificate -a -p /Library/Keychains/System.keychain              >  "$TMP"
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> "$TMP"

n=$(grep -c 'BEGIN CERTIFICATE' "$TMP")
[ "$n" -gt 0 ] || { echo "証明書を1件も取り出せなかった（キーチェーンのパスを確認すること）" >&2; exit 1; }

mv "$TMP" "$OUT"; trap - EXIT
chmod 644 "$OUT"
echo "証明書 ${n} 件 → ${OUT/#$HOME/~}"

if security find-certificate -a -c "Cisco Umbrella" /Library/Keychains/System.keychain >/dev/null 2>&1; then
  echo "Cisco Umbrella Root CA を確認（この端末は TLS 傍受下）"
else
  echo "Cisco Umbrella Root CA は無し（傍受されていない環境。このバンドルは無害だが不要）"
fi
