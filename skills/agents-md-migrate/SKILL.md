---
name: agents-md-migrate
description: プロジェクトの指示ファイルを CLAUDE.md から AGENTS.md へ移す。「AGENTS.md に移して」「CLAUDE.md を AGENTS.md にして」「指示ファイルを移行して」と言われたとき、インストーラが「CLAUDE.md が残っている」と知らせたとき、CLAUDE.md と AGENTS.md が同じフォルダに両方あるときに使う。
---

# agents-md-migrate

プロジェクトの指示ファイルの名前を `CLAUDE.md` から `AGENTS.md` に替える。`AGENTS.md` は Claude Code（2.1.277 以降）・Codex・Antigravity（agy）が共通に読む名前で、1本書けばどの道具にも効く。

Claude Code は、起動したフォルダとその上のどこかに `CLAUDE.md` が1つでもあると、既定では `AGENTS.md` を全部読まない。混ざった状態を残さないことが、このスキルの目的。インストーラ（`install.py`）は `settings.json` に両方を読む設定（`pluginConfigs` の `cc-plugin-agents-md@builtin`）を入れるが、それは移す途中の取りこぼしを防ぐためのもので、移し終えるのが本筋。

## 手順

1. 計画を出す：`python3 ~/.claude/skills/agents-md-migrate/scripts/migrate.py`（探す場所は既定で `~/Projects`。違うなら `--root <場所>`）
2. 計画をユーザーに見せる。改名するファイル、書き換える md、飛ばすものとその理由を、リポジトリごとに短くまとめる
3. ユーザーが了承したら `--apply` を付けて実行する。リポジトリが GitHub などとつながっていて、ユーザーが push してよいと言ったら `--push` も付ける
4. 飛ばしたものを片付ける
   - 同じフォルダに `AGENTS.md` もある：両方を読み、`CLAUDE.md` の内容を `AGENTS.md` へまとめてから `CLAUDE.md` を消す（`git rm`）。食い違う記述はユーザーに確かめる
   - 未コミットの変更がある：ユーザーにコミットしてよいか確かめ、コミットしてから再実行する
5. 最後に計画をもう一度出し、「移す予定 0 件」と飛ばすものが無いことを確かめて報告する

## 守ること

- グローバルの `~/.claude/CLAUDE.md` は移さない。対象はプロジェクトのフォルダの中の `CLAUDE.md` だけ
- 他者のリポジトリの写し（`reference`・`legacy`・`vendor` の下）と隠しフォルダは触らない
- コミットは改名と書き換えたファイルだけを入れる（スクリプトがそうする）。ほかの未コミットの変更を巻き込むコミットを自分で作らない
- 共有のリポジトリ（チームで使うもの）を移すと、ほかの人の端末にも pull で届く。push する前に、そのリポジトリの持ち主が移してよいと言っているか確かめる
