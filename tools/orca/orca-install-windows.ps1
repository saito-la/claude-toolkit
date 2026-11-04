# orca-install-windows.ps1 — Windows に Orca を利用者の権限で入れる（管理者権限は要らない）。
#
#   pwsh -NoProfile -File <このフォルダ>/orca-install-windows.ps1 [-Version 1.4.247]
#
# 何度実行しても同じ状態になる（入っている版が同じなら取り直さない）。2026-11-04、311C4W991 で検証。
#
# 1. GitHub の配布物（orca-windows-setup.exe）を取り、Authenticode の署名（SignPath Foundation）を
#    確かめて、/S で入れる。入る先は %LOCALAPPDATA%\Programs\orca（利用者ごとの NSIS）
# 2. 利用者の環境変数 ORCA_TELEMETRY_DISABLED=1 を置く（利用統計を送らない）
# 3. orca CLI のフォルダ（resources\bin）を利用者の PATH に足す
# 4. Orca を起動し、つながったら ~/Projects のリポジトリを登録する（orca-register-projects.py）
#
# TLS の傍受への対処は、この端末の利用者の環境変数が受け持つ。Orca（Electron）は起動時の環境を
# そのまま受け継ぐので、NODE_EXTRA_CA_CERTS か NODE_OPTIONS=--use-system-ca が利用者の環境変数に
# あれば効く（akiko-office の docs/claude-code-environment.md「TLS 傍受下での証明書検証」）。
#
# Claude Code の中から実行すると、起動する Orca が CLAUDE_CODE_CHILD_SESSION などを受け継ぎ、
# Orca の中で開いた claude が子のセッション扱いになって記録を残さない。起動の前にそれらを外す
# （pj-platform.sh の PJ_PS_CLEAN_ENV と同じ規則）。
#
# 画面での設定（権限・一覧の表示）は最後に案内を出す。中身の正本は README.md「画面での設定」。
param([string]$Version = "1.4.247")
$ErrorActionPreference = "Stop"

$Signer = "CN=SignPath Foundation"
$AppDir = Join-Path $env:LOCALAPPDATA "Programs\orca"
$AppExe = Join-Path $AppDir "Orca.exe"
$BinDir = Join-Path $AppDir "resources\bin"
$Cli = Join-Path $BinDir "orca.exe"
function Say($m) { Write-Host "==> $m" }

# ---- 1. アプリ ----
# ファイルの版は 1.4.247.0 の4桁で入っているので、末尾の .0 を落として比べる
$have = if (Test-Path $AppExe) { (Get-Item $AppExe).VersionInfo.ProductVersion -replace '\.0$', '' } else { "" }
if ($have -eq $Version) {
  Say "Orca $Version は入っている（$AppDir）"
} else {
  if (Get-Process Orca -ErrorAction SilentlyContinue) { throw "Orca が起動している。終了してから実行する" }
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("orca-install-" + [guid]::NewGuid())
  New-Item -ItemType Directory $tmp | Out-Null
  $setup = Join-Path $tmp "orca-windows-setup.exe"
  Say "取得：v$Version orca-windows-setup.exe"
  Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/stablyai/orca/releases/download/v$Version/orca-windows-setup.exe" -OutFile $setup
  $sig = Get-AuthenticodeSignature $setup
  if ($sig.Status -ne "Valid") { throw "署名が有効でない（$($sig.Status)）" }
  if (-not $sig.SignerCertificate.Subject.StartsWith($Signer)) { throw "署名者が違う（$($sig.SignerCertificate.Subject)）" }
  Say "署名を確かめた：$($sig.SignerCertificate.Subject)"
  $p = Start-Process $setup -ArgumentList "/S" -PassThru -Wait
  if ($p.ExitCode -ne 0) { throw "インストーラが失敗した（終了コード $($p.ExitCode)）" }
  Remove-Item -Recurse -Force $tmp
  if (-not (Test-Path $Cli)) { throw "入れた後に orca CLI が見つからない：$Cli" }
  Say "入れた：$AppDir（$have → $Version）"
}

# ---- 2. 利用統計を止める ----
if ([Environment]::GetEnvironmentVariable("ORCA_TELEMETRY_DISABLED", "User") -ne "1") {
  [Environment]::SetEnvironmentVariable("ORCA_TELEMETRY_DISABLED", "1", "User")
}
$env:ORCA_TELEMETRY_DISABLED = "1"
Say "利用者の環境変数：ORCA_TELEMETRY_DISABLED=1"

# ---- 3. PATH ----
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (($userPath -split ";") -contains $BinDir)) {
  [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $BinDir), "User")
  Say "利用者の PATH に足した：$BinDir（新しく開いた端末から効く）"
}
if (-not (($env:Path -split ";") -contains $BinDir)) { $env:Path += ";" + $BinDir }

# ---- 4. 起動と登録 ----
if (-not (Get-Process Orca -ErrorAction SilentlyContinue)) {
  Get-ChildItem Env: | Where-Object {
    $_.Name -match "^(CLAUDE|AI_AGENT$)" -and
    $null -eq [Environment]::GetEnvironmentVariable($_.Name, "User") -and
    $null -eq [Environment]::GetEnvironmentVariable($_.Name, "Machine")
  } | ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) }
  Start-Process $AppExe
}
Say "Orca の起動を待つ"
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  if ((& $Cli status --json 2>$null | Out-String) -match '"reachable": true') { $ok = $true; break }
  Start-Sleep 2
}
if (-not $ok) { throw "Orca につながらない。画面で初回の確認を済ませてから、もう一度実行する" }

$py = (Get-Command python3, python, py -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $py) { throw "Python が見つからない。リポジトリの登録は飛ばした" }
$env:PYTHONUTF8 = "1"
& $py (Join-Path $PSScriptRoot "orca-register-projects.py")

@"

==> 画面で行うこと（正本は README.md「画面での設定」）
  0. 初回の「デフォルトの Agent を選択」で Claude を選び、Yolo モードはオフのままにする
  1. Settings → Agents → Permissions を「Each agent's own settings」にする（Full access のままにしない）
  2. Settings → Git & Source Control で、作業場を作るときに元のブランチを最新にする設定をオンにする
  3. 左の一覧の Workspace options：Group by を None、Sort by を Agent Activity、Hide sleeping をオン、Except default branch をオフ
"@ | Write-Host
