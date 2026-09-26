# rtk

**この収録は 2026-08-01 に廃止した。導入している場合は下記の手順で削除すること。**

正式名称 RTK（Rust Token Killer）。Claude Code の bash コマンド出力をフックで自動的に圧縮し、トークン消費を削減する CLI プロキシ。本体は外部 OSS（[rtk-ai/rtk](https://github.com/rtk-ai/rtk)、Apache-2.0、Rust製）で、このリポジトリにはセットアップ手順のみを置いていた。

セットアップ手順の収録期間は 2026-07-27 から 2026-08-01 まで。この間にこのリポジトリまたは `crpc-tools` の手順で導入した場合が対象。

## 廃止の理由

出力が減るのではなく、**出力が別の内容に静かに置き換わる**障害が確認された。

`grep '^path = ' <file>` の結果が `61 matches in 1 files:` という要約文に置換され、それをパイプで受けた集計が「対象ファイル 0 件」という誤った答えを返した。フックは全 bash 呼び出しに無条件で掛かるため、件数・パス一覧・存在確認といった**プログラムが消費する出力**が壊れ、しかも失敗が沈黙する。同じ調査では `cat -A` が本来の cat と非互換で失敗し、rtk 自身が `Broken pipe` で panic する事象も出た。

トークン節約の失敗は回復できるが、誤った数値を返す層は bash 由来のあらゆる集計の信頼性を損なう。ファイル内容の圧縮（平均約3割の省略）も、名簿や一次データを逐字で読む用途と両立しない。

条件を付けて一部だけ自動適用する案も検討したが、省略が有害かどうかは「そのとき何を探しているか」で決まり、フックの入力（コマンド文字列）には含まれないため判定できない。削減率が最大だったのは staged diff の 98.8% で、これは全文を読むべき対象であり、高い削減率はそのまま「隠された量」を意味していた。

出力が大きいときは、要約ではなく問いを狭める（`git diff --stat`、`git log --oneline`、`grep -c`、`ps -p <PID>` 等）。

## 削除手順

`rtk init -g --uninstall` だけでは残留することがある。`rtk init -g --auto-patch` はフックを重複登録する場合があり（旧版のこの README でも注意していた）、rtk 自身の設置検出も当てにならない（フックが実在するのに `No hook installed` と報告した実例がある）。そのため、設定ファイルを直接検査するスクリプトを用意した。

`settings.json` を手で編集する必要はない。JSON を壊すと Claude Code が起動しなくなるため、スクリプトを使うこと。

スクリプトの場所は導入の形で違う。`saito-la/claude` 経由なら `~/Projects/saito-la/claude/vendor/claude-toolkit/tools/rtk/remove-rtk.py`、直 clone なら `~/claude-toolkit/tools/rtk/remove-rtk.py`。以下は前者で書く。Windows では `python3` を `py` か `python` に読み替える。

1. 何が残っているかを確認する。何も書き換えない。

```bash
python3 ~/Projects/saito-la/claude/vendor/claude-toolkit/tools/rtk/remove-rtk.py
```

2. 「対応は不要」と出れば設定は残っていない。手順3の本体の確認へ進む。残っていれば削除を実行する。バックアップを作ってから書き換える。

```bash
python3 ~/Projects/saito-la/claude/vendor/claude-toolkit/tools/rtk/remove-rtk.py --apply
```

3. rtk 本体を削除する。設定が残っていなくても本体だけ残っていることがあるので、必ず確かめる。

Mac：

```bash
type -a rtk                 # 何も出なければ本体は無い
brew uninstall rtk          # brew で入れた場合
rm -f ~/.local/bin/rtk      # curl スクリプトで入れた場合
```

`brew uninstall` が `No such keg` を返すのに `/opt/homebrew/bin/rtk` が見えるときは、brew のパッケージではなく `~/.local/bin/rtk` を指す手作りのリンクである（2026-09-26 に実例）。`ls -l` でリンク先を確かめて消す。データの置き場 `~/Library/Application Support/rtk` も消す。

Windows（PowerShell）：

```powershell
Get-Command rtk -All -ErrorAction SilentlyContinue   # 何も出なければ本体は無い
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\rtk" -ErrorAction SilentlyContinue
$p = [Environment]::GetEnvironmentVariable("PATH","User")
[Environment]::SetEnvironmentVariable("PATH", (($p -split ';') | Where-Object { $_ -and $_ -notmatch '\\rtk\\?$' }) -join ';', "User")
```

4. プロジェクト側の残りを探す。スクリプトが見るのは `~/.claude/` だけで、`rtk init`（`-g` なし）はプロジェクトの `CLAUDE.md` に `<!-- rtk-instructions -->` 〜 `<!-- /rtk-instructions -->` の塊を書き込む。これが残っていると「全コマンドに `rtk` を付けよ」という指示として読まれる。各リポジトリで `git grep -n -i rtk` を実行し、この塊と、`.claude/settings.local.json` の `Bash(rtk …)` の許可を取り除く。過去の作業ログなど記録として残すものは消さなくてよい。

5. Claude Code を再起動する。

6. 手順1を再実行して「対応は不要」と出ること、手順3の確認コマンドが何も出さないことを確かめる。

スクリプトが触るのは `~/.claude/` の `settings.json`・`settings.local.json`・`CLAUDE.md`・`RTK.md` のみで、rtk に言及するフック・許可設定と `@RTK.md` のインポート行だけを取り除く。他のフック・`model`・`statusLine`・その他の許可設定は保持する。書き換えたファイルは同じディレクトリに `.bak-<日時>` として退避する。何度実行しても同じ結果になる。

`settings.json` を dotfiles でコピー運用している場合は、削除後にリポジトリ側へ反映する（ローカル実体のみが変わるため）。
