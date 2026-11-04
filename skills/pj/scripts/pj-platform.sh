# pj-platform.sh — pj のスクリプトが source する、Mac と Windows（Git Bash）の差を吸収する部品。
#
#   source "$(dirname "${BASH_SOURCE[0]}")/pj-platform.sh"
#
# 持つもの:
#   PJ_WIN             Windows の Git Bash なら 1、それ以外は 0
#   pj_posix <path>    パスを bash で比べられる形にする。Windows では C:/… や C:\… を /c/… に直す
#                      （git は C:/… を返し、$HOME は /c/… なので、そのまま比べると一致しない）
#   pj_win <path>      Windows のネイティブの exe に渡す形（C:\…）。Mac ではそのまま返す
#   pj_find_orca       orca CLI のパスを出す（見つからなければ空）
#   pj_live_cwds       稼働中の claude の作業フォルダを1行1件で出す（pj_posix の形）
#
# Windows の作り（2026-11-04、311C4W991 で検証）:
# - 稼働中のセッションは ps と lsof では取れない（Git Bash の ps は MSYS のプロセスしか見えず、
#   lsof は無い）。Claude Code は稼働中のセッションを ~/.claude/sessions/<pid>.json に作業フォルダ
#   ごと書くので、それを読み、tasklist でまだ生きている pid に絞る
# - Python の標準出力は既定で cp932 になり、日本語が化ける。PYTHONUTF8=1 を立てて UTF-8 にする

PJ_WIN=0
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) PJ_WIN=1 ;;
esac

if (( PJ_WIN )); then
  export PYTHONUTF8=1
fi

# 新しく起動する claude の前に、今のセッションから受け継いだ変数を外す PowerShell の1行。
# Windows では窓や端末が起動元の環境を受け継ぐため、Claude Code の中から開くと
# CLAUDE_CODE_CHILD_SESSION などが残り、開いた先が子のセッション扱いになって記録を残さない
# （2026-11-04 に「Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker」で確認）。
# 利用者やマシンの環境変数に置いた CLAUDE_* は外さない。Mac は iTerm2 と Orca が新しい環境で
# 起動するので要らない
PJ_PS_CLEAN_ENV='Get-ChildItem Env: | Where-Object { $_.Name -match "^(CLAUDE|AI_AGENT$)" -and $null -eq [Environment]::GetEnvironmentVariable($_.Name, "User") -and $null -eq [Environment]::GetEnvironmentVariable($_.Name, "Machine") } | ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) }'

pj_posix() {
  if (( PJ_WIN )) && [[ -n "${1:-}" ]]; then
    cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1"
  else
    printf '%s\n' "${1:-}"
  fi
}

pj_win() {
  if (( PJ_WIN )) && [[ -n "${1:-}" ]]; then
    cygpath -w "$1" 2>/dev/null || printf '%s\n' "$1"
  else
    printf '%s\n' "${1:-}"
  fi
}

pj_find_orca() {
  local c
  c="$(command -v orca 2>/dev/null || true)"
  if [[ -z "$c" ]]; then
    # brew の cask は /Applications、管理者権限の無い Mac（orca-install-user.sh）は ~/Applications、
    # Windows の利用者向けインストーラ（orca-install-windows.ps1）は %LOCALAPPDATA%\Programs\orca
    local cands=(/Applications/Orca.app/Contents/Resources/bin/orca "$HOME/Applications/Orca.app/Contents/Resources/bin/orca")
    if (( PJ_WIN )) && [[ -n "${LOCALAPPDATA:-}" ]]; then
      cands+=("$(pj_posix "$LOCALAPPDATA")/Programs/orca/resources/bin/orca.exe")
    fi
    for c in "${cands[@]}"; do
      [[ -x "$c" ]] && break
      c=""
    done
  fi
  printf '%s\n' "$c"
}

pj_live_cwds() {
  if (( PJ_WIN )); then
    local d="$HOME/.claude/sessions"
    [[ -d "$d" ]] || return 0
    # tasklist の CSV の2列目が pid。生きている pid の記録だけを読む
    tasklist.exe //FO CSV //NH 2>/dev/null | cut -d, -f2 | tr -cd '0-9\n' > "${TMPDIR:-/tmp}/pj-live-pids.$$" || return 0
    python3 - "$d" "${TMPDIR:-/tmp}/pj-live-pids.$$" <<'PY' | while IFS= read -r c; do pj_posix "$c"; done
import glob, json, os, sys
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
alive = set(open(sys.argv[2], encoding="ascii", errors="ignore").read().split())
for f in glob.glob(os.path.join(sys.argv[1], "*.json")):
    try:
        s = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if str(s.get("pid")) in alive and s.get("cwd"):
        print(s["cwd"].replace(os.sep, "/"))
PY
    rm -f "${TMPDIR:-/tmp}/pj-live-pids.$$"
    return 0
  fi
  # `pgrep -x claude` は使わない。取りこぼす（2026-09-02 の実測で、稼働中の
  # セッション自身の pid を落とした。原因は pgrep が自分の祖先を既定で除外すること
  # で、-a で含められる）。`ps` で全プロセスを見て comm のベース名で拾う。
  # comm には空白を含むパスが入り得るので $2 では切らない。
  local pids
  pids="$(ps -Ao pid=,comm= 2>/dev/null \
    | awk '{ pid=$1; $1=""; sub(/^ +/,""); n=$0; sub(/.*\//,"",n); if (n=="claude") print pid }' \
    | tr '\n' ',' | sed 's/,$//')"
  [[ -n "$pids" ]] || return 0
  lsof -a -d cwd -p "$pids" -Fn 2>/dev/null | sed -n 's/^n//p'
}

# 稼働中の claude の pid のうち、作業フォルダが <dir> の中にあるもの（Windows だけ。wt-finish.sh の
# 遅らせた片付けが、そのセッションの終わりを待つのに使う）
pj_live_pids_under() {
  (( PJ_WIN )) || return 0
  local d="$HOME/.claude/sessions" dir
  dir="$(pj_win "$1")"
  [[ -d "$d" ]] || return 0
  tasklist.exe //FO CSV //NH 2>/dev/null | cut -d, -f2 | tr -cd '0-9\n' > "${TMPDIR:-/tmp}/pj-live-pids.$$" || return 0
  python3 - "$d" "${TMPDIR:-/tmp}/pj-live-pids.$$" "$dir" <<'PY'
import glob, json, os, sys
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
alive = set(open(sys.argv[2], encoding="ascii", errors="ignore").read().split())
root =os.path.normcase(os.path.normpath(sys.argv[3]))
for f in glob.glob(os.path.join(sys.argv[1], "*.json")):
    try:
        s = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    cwd = os.path.normcase(os.path.normpath(s.get("cwd") or ""))
    if str(s.get("pid")) in alive and (cwd == root or cwd.startswith(root + os.sep)):
        print(s["pid"])
PY
  rm -f "${TMPDIR:-/tmp}/pj-live-pids.$$"
}
