# claude-toolkit

Claude Code用の汎用Skill集。プロジェクト非依存のツール群。

## 収録スキル

| スキル | 内容 |
|---|---|
| [markdown-export](skills/markdown-export/SKILL.md) | markdown → Word(.docx) / PDF 変換 |
| [markdown-to-gdocs](skills/markdown-to-gdocs/SKILL.md) | markdown/docx → Google Docs アップロード＋体裁適用（要・自分のGoogle Cloud OAuthセットアップ） |
| [transcribe-meeting](skills/transcribe-meeting/SKILL.md) | 会議録音 → 議事録3点セット（原文・ケバ取り版・凝縮版）自動生成（要・Gemini APIキー） |
| [mcp-setup](skills/mcp-setup/SKILL.md) | Claude CodeへのMCPサーバー接続・セットアップ手順を案内 |
| [format-prompt](skills/format-prompt/SKILL.md) | 粗いプロンプトを7ブロックの型に整形 |
| [interest-profile](skills/interest-profile/SKILL.md) | 会話履歴からユーザーの興味プロファイルを生成・更新 |
| [meishi-rename](skills/meishi-rename/SKILL.md) | 名刺スキャンPDFのファイル名をOCR結果から整形 |
| [person-research](skills/person-research/SKILL.md) | 人物調査URLからレジストリを横断調査し根拠付きレポートを作成 |
| [schedule-reply](skills/schedule-reply/SKILL.md) | 日程調整メール(tonton/調整さん)にGoogleカレンダーの空きで自動回答（要・個別セットアップ、上級者向け） |
| [email-to-calendar](skills/email-to-calendar/SKILL.md) | 開催案内メールから日時・場所・URLを抽出しGoogleカレンダーへ登録。差出人別ルールを自己蓄積し継続利用で精度向上（要・`RULES.example.md`を`RULES.md`にコピー） |

## インストール

各スキルディレクトリを `~/.claude/skills/<name>/` へ配置する（symlinkでもコピーでも可）。

```bash
git clone <このリポジトリ> ~/claude-toolkit
mkdir -p ~/.claude/skills
ln -sf ~/claude-toolkit/skills/markdown-export     ~/.claude/skills/markdown-export
ln -sf ~/claude-toolkit/skills/markdown-to-gdocs   ~/.claude/skills/markdown-to-gdocs
ln -sf ~/claude-toolkit/skills/transcribe-meeting  ~/.claude/skills/transcribe-meeting
ln -sf ~/claude-toolkit/skills/mcp-setup           ~/.claude/skills/mcp-setup
ln -sf ~/claude-toolkit/skills/format-prompt       ~/.claude/skills/format-prompt
ln -sf ~/claude-toolkit/skills/interest-profile    ~/.claude/skills/interest-profile
ln -sf ~/claude-toolkit/skills/meishi-rename       ~/.claude/skills/meishi-rename
ln -sf ~/claude-toolkit/skills/person-research     ~/.claude/skills/person-research
ln -sf ~/claude-toolkit/skills/schedule-reply      ~/.claude/skills/schedule-reply
ln -sf ~/claude-toolkit/skills/email-to-calendar   ~/.claude/skills/email-to-calendar
```

配置後、Claude Codeが会話の文脈（「Wordにして」「PDFにして」等）から自動的にスキルを発見する。明示的にコマンドを打つ場合は各SKILL.mdの使い方を参照。

`schedule-reply` は Google Calendar/Gmail への書き込み・自動送信を伴うため、配置後も `skills/schedule-reply/scripts/README.md` に沿った個別セットアップ（`config.json`作成・Google認証・Playwright）が別途必要。

`email-to-calendar` は初回のみ `skills/email-to-calendar/RULES.example.md` を同ディレクトリの `RULES.md` にコピーして使い始める（差出人別ルールが蓄積されるファイルのため`.gitignore`済み）。

## 依存関係

- `markdown-export`：pandoc, python-docx, lxml, pymupdf, Google Chrome（PDF生成）
- `markdown-to-gdocs`：Node.js, 自分のGoogle Cloud OAuthクライアント（詳細はSKILL.md参照）
- `transcribe-meeting`：google-genai, ffmpeg/ffprobe, Gemini APIキー（詳細はSKILL.md参照）
- `interest-profile`：Python3（`scripts/extract_interests.py`）
- `mcp-setup`・`format-prompt`・`meishi-rename`・`person-research`：追加依存なし（`person-research`はWebFetch/WebSearch・Agentツールを使用）
- `schedule-reply`：Node.js, Playwright, 自分のGoogle OAuth認証情報（詳細はSKILL.md参照）
- `email-to-calendar`：Gmail/Calendar MCPツール（環境に設定されたもの）。追加ソフトウェア依存なし

## その他のツール

Skill（自動発見）ではなく、個別にセットアップして使うツール。

| ツール | 内容 |
|---|---|
| [statusline](tools/statusline/README.md) | Claude Codeのターミナル下部に使用状況（コンテキスト・レート制限・作業フォルダ・アカウント）を表示 |
| [gmail-message-id-bookmarklet](tools/gmail-message-id-bookmarklet/README.md) | Gmailで開いているメールのMessage-IDをクリップボードにコピーするブックマークレット（Claudeにメールを一意に伝えるため） |
