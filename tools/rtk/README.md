# rtk

正式名称 RTK（Rust Token Killer）。Claude Codeのbashコマンド出力をフックで自動的に圧縮し、トークン消費を60〜90%削減するCLIプロキシ。本体は外部OSS（[rtk-ai/rtk](https://github.com/rtk-ai/rtk)、Apache-2.0、Rust製）で、このリポジトリにはセットアップ手順のみを置く。

`git status` → `rtk git status` のようにコマンドが透過的に書き換えられ、通常の呼び出し方は変わらない。

## セットアップ

**macOS（Homebrew、推奨）：**

```bash
brew install rtk
rtk init -g --auto-patch
```

**その他（curlスクリプト、`~/.local/bin`に配置）：**

```bash
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh
rtk init -g --auto-patch
```

`rtk init -g` が `~/.claude/RTK.md` の生成、`~/.claude/settings.json` へのPreToolUse(Bash)フック追加、`~/.claude/CLAUDE.md` への `@RTK.md` 参照追加を行う。設定後はClaude Codeを再起動する。

## 動作確認

```bash
rtk --version   # rtk X.Y.Z が出ること
rtk gain        # ダッシュボードが表示されること（command not found にならない）
which rtk       # 正しいバイナリを指しているか確認
```

Claude Code内で `git status` 等を実行させ、`rtk gain` の使用履歴に記録されれば正常動作。

## 落とし穴

- **名前衝突**：`brew search rtk` は `reachingforthejack/rtk`（Rust Type Kit・別物）とは異なる。`brew info rtk` の説明文が「CLI proxy to minimize LLM token consumption」であることを確認してからインストールする。
- **既存のガード付きhookがある環境で `rtk init -g --auto-patch` を実行すると、`PreToolUse.Bash` にフックが重複登録されることがある**（`command -v rtk >/dev/null 2>&1 && rtk hook claude || exit 0` という既存の無害化ガード付きエントリに加え、`rtk hook claude` 単体のエントリが追記される）。`~/.claude/settings.json` の `hooks.PreToolUse` を確認し、重複していれば片方を削除する。
- `~/.claude/settings.json`・`~/.claude/CLAUDE.md` をdotfilesでcopy運用している場合、`rtk init -g` の変更はローカル実体にのみ反映される。他マシンへ伝播させるには、通常のdotfiles同期フロー（上り→リポジトリ反映）を通す。

## アンインストール

```bash
rtk init -g --uninstall
brew uninstall rtk
```
