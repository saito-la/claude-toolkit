#!/usr/bin/env bash
# pj-go.sh — /pj の高速パス。索引更新→ルーティングを1コマンドで行い、
# DIRECT（L1 の決定的語彙一致）なら確認なしで即起動する。
#
# 目的: ドンピシャ引数のとき、モデルが index更新・route・relpath実在確認・
# ナレーションを多段で行う往復（実測47秒）を、Bash 1回に畳み込んで排除する。
# DIRECT は L1（完全一致／exact-alias／単一 path 一致）からしか返らないため、
# 自動起動しても誤起動リスクはその確信範囲に限定される。
#
# Usage:
#   pj-go.sh "<query>" [-c] [--prompt-file <file>]
#     <query>  やりたいこと（原文）。用件を含んでいれば、開いた先への最初の指示になる
#              （pj-open.sh --request。行き先の名前だけなら渡さない）
#     -c       新規ではなく直近セッションを継続（pj-open.sh へ委譲）
#     --prompt-file  原文の代わりに最初の指示にする文（/pj が作業を移すときの起動文）。
#              DIRECT でなければ使わずに残すので、呼び出し側が pj-open.sh に渡し直す
#
# 出力（stdout 1行目で分岐）:
#   OPENED<TAB><relpath>     即起動した（モデルはこの1行を報告するだけでよい）
#   CANDIDATES               確信が割れた。2行目以降 <relpath>\t<reason>（モデルが文脈判断へ）
#   CATALOG                  語彙一致なし。2行目以降に全プロジェクトの圧縮カタログ
#                            （約4kトークン）。モデルが会話文脈で判断する
# 終了コード: 0=OPENED / 10=CANDIDATES または CATALOG / 1=引数不正 / 2=起動失敗
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECOMMEND="$SCRIPT_DIR/pj-recommend.py"
ROUTER="$SCRIPT_DIR/pj-router.py"
OPEN="$SCRIPT_DIR/pj-open.sh"

query=""
cont=""
prompt_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--continue) cont="-c"; shift ;;
    --prompt-file) prompt_file="${2:-}"; shift 2 ;;
    -*) echo "pj-go.sh: 不明なオプション: $1" >&2; exit 1 ;;
    *) if [[ -z "$query" ]]; then query="$1"; shift; else echo "pj-go.sh: 引数が多すぎます: $1" >&2; exit 1; fi ;;
  esac
done

if [[ -z "$query" ]]; then
  echo "pj-go.sh: <query> が必要です（例: pj-go.sh \"recruitmentの作業\"）" >&2
  exit 1
fi

# 索引を最新化（差分がなければ即返る）
python3 "$RECOMMEND" index >/dev/null 2>&1 || true

# ルーティング判定
route_out="$(python3 "$ROUTER" route "$query")"
first_line="$(printf '%s\n' "$route_out" | head -n1)"

if [[ "$first_line" == DIRECT$'\t'* ]]; then
  relpath="${first_line#DIRECT$'\t'}"
  # DIRECT は確信済み → 確認なしで即起動（pj-open.sh が dir 実在を保証）
  # 継続（-c）は前の会話に戻るだけなので、原文を依頼として渡さない
  open_args=("$relpath")
  if [[ -n "$cont" ]]; then open_args+=("$cont"); else open_args+=(--request "$query"); fi
  [[ -n "$prompt_file" ]] && open_args+=(--prompt-file "$prompt_file")
  if bash "$OPEN" "${open_args[@]}" >&2; then
    printf 'OPENED\t%s\n' "$relpath"
    exit 0
  else
    echo "pj-go.sh: 起動に失敗しました: $relpath" >&2
    exit 2
  fi
fi

# DIRECT でなければ判定材料をそのまま返す（モデルが文脈判断へ）
printf '%s\n' "$route_out"
exit 10
