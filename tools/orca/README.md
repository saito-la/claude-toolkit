# Orca

作成日：2026-11-04

## 1. Orca の性質

- Stably の Orca。複数の AI エージェントを並べて動かすデスクトップアプリで、MIT ライセンスのオープンソースである。[公式サイト](https://www.onorca.dev/)、[GitHub](https://github.com/stablyai/orca)
- 1つの作業ごとに git worktree を1つ作り（作業場）、その中で Claude Code や Codex の CLI を動かす。モデルを売るものではなく、手元に入っている CLI と契約をそのまま使う。Claude Code は `~/.claude` の設定を読むので、今のフックもそのまま効く
- 作業場は `~/orca/workspaces/<リポジトリ>/<名前>` に置かれ、ブランチ名には設定の接頭辞（既定は git のユーザー名）が付く。作業場には git に入っているファイルしか無い
- 初回の起動で、`~/.claude/settings.json` の hooks の15のイベントに状態を知らせるフックを足す（Codex には `~/.codex/hooks.json`）。送る先は手元の Orca（127.0.0.1）だけで、Orca の外で動くセッションでは何もしない。止めるときは Settings → Agents → Agent status hooks をオフにするか、`orca agent hooks off`
- CLI（`orca`）があり、pj（`skills/pj`）はこれを使って作業場を作り、claude を起動する
- 匿名の利用統計を PostHog（米国）へ送る。プロンプト、端末の中身、パス、リポジトリ名は送らないと書かれている。止め方は Settings → Privacy の「Share anonymous usage data」をオフにするか、環境変数 `ORCA_TELEMETRY_DISABLED=1` か `DO_NOT_TRACK=1` を付けて起動する。環境変数で止めて起動している間は、画面のスイッチは動かせない

## 2. 入れ方

### 2.1. Homebrew が使える Mac

```bash
brew tap stablyai/orca
brew trust --cask stablyai/orca/orca
brew install --cask stablyai/orca/orca
```

外部の tap は信用されないので、この cask だけを信用する（tap 全体は信用しない）。アプリは Developer ID（Lovecast LLC、6CX3WHS9HZ）で署名され、公証も済んでいる。`orca` CLI は `/opt/homebrew/bin/orca` に入る。入れた後、普通に起動して Settings → Privacy で利用統計をオフにする。

### 2.2. 管理者権限も Homebrew も無い Mac

```bash
bash <このフォルダ>/orca-install-user.sh
```

GitHub の配布物を取り、署名と公証を確かめて `~/Applications/Orca.app` に置き、`orca` を `~/.local/bin` に張る。GUI へ `ORCA_TELEMETRY_DISABLED=1` と、TLS を傍受する網なら `NODE_EXTRA_CA_CERTS` を渡す LaunchAgent（`com.saito.orca-env`）を入れる。最後に Orca を起動し、リポジトリを登録する。何度実行しても同じ状態になる。初回は Orca の画面で確認を求められることがあり、そのときは画面で済ませてからもう一度実行する。

### 2.3. Windows

```powershell
pwsh -NoProfile -File <このフォルダ>/orca-install-windows.ps1
```

管理者権限は要らない。GitHub の `orca-windows-setup.exe` を取り、Authenticode の署名（SignPath Foundation）を確かめて `/S` で入れる。入る先は `%LOCALAPPDATA%\Programs\orca` で、`orca` CLI はその `resources\bin` にあり、利用者の PATH に足す。利用者の環境変数 `ORCA_TELEMETRY_DISABLED=1` を置き、最後に Orca を起動してリポジトリを登録する。何度実行しても同じ状態になる。

- TLS の傍受への対処は、利用者の環境変数（`NODE_EXTRA_CA_CERTS` か `NODE_OPTIONS=--use-system-ca`）がそのまま Orca に受け継がれる。Mac の LaunchAgent に当たるものは要らない
- Claude Code の中から Orca を起動すると、Orca が `CLAUDE_CODE_CHILD_SESSION` などを受け継ぎ、Orca の画面から開いた claude が記録を残さない。スクリプトはそれらを外して起動する。手で起動するならスタートメニューから起動する
- 作業場は Mac と同じく `~/orca/workspaces/<リポジトリ>/<名前>` に置かれる。pj から開くときの違いは `skills/pj/README.md`「9. Windows」

## 3. 画面での設定

入れたら Orca の画面で次を行う。設定は端末ごとに持つ。初回の起動で「デフォルトの Agent を選択」が出たら Claude を選び、Yolo モードはオフのままにする（1 と同じ理由）。

1. Settings → Agents → Permissions を「Each agent's own settings」にする。既定の Full access は Claude Code を `--dangerously-skip-permissions` で起動し、確認なしで何でも実行させる。各エージェントの設定（Claude Code は `settings.json` の `permissions.defaultMode`）に従わせ、権限の決まりを Orca 側に二つ目として持たない。Bypass Permissions の確認画面が出たら No, exit を選ぶ
2. Settings → Git & Source Control で、作業場を作るときに元のブランチを最新にする設定をオンにする
3. 左の一覧の Workspace options で、Group by を None、Sort by を Agent Activity、Hide sleeping をオン、Except default branch をオフにする。動いている作業場だけが上にまとまり、登録したリポジトリの本体は隠れる
4. 左下の「Add a setup script」は使わずに閉じてよい

作業場の左の黄色い回る輪は、その作業場のエージェントのどれかが動いていることを示す。裏で動くサブエージェントが残っていても回り続け、フックが30分届かなければ止まる。

## 4. リポジトリの登録

```bash
python3 <このフォルダ>/orca-register-projects.py [--dry-run]
```

pj の索引（`~/Projects` の下で `CLAUDE.md` を持つフォルダ）を git リポジトリの最上位にまとめ、未登録のものを Orca に登録する。別の一覧を使うときは `--stdin` で `~/Projects` からの相対パスを渡す。Orca が動いていなければ何もしない。登録を外すコマンドは無く、外すときは画面で行う。pj は未登録のリポジトリを開くときにその場で登録するので、この一括登録は一覧に並べるためのものである。

## 5. 使い方

- 作業を始めるときは pj（「〇〇のプロジェクトを開いて」、`/pj`）を使う。Orca が動いていれば Orca の作業場で claude が起動し、動いていなければ iTerm2（Windows では Windows Terminal）で開く
- 作業を終えるときは、作業場の中のセッションで「セッションを終わります」と言う。セッション終了の手順が `skills/pj/scripts/wt-finish.sh` を呼び、作業場の成果を既定ブランチへ統合して push し、作業場とブランチを片付ける
