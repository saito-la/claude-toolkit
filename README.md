# claude-toolkit

Claude Code用の汎用Skill・規約集。プロジェクト非依存のツール群。

## 収録規約

`conventions/` は「どの組織・どの案件でも通用する作業規約」を置く。Claude Code から `~/.claude/conventions/<name>.md` として参照される前提で書き、個人・組織固有の絶対パス・実名・内部文書の節番号は含めない。

| 規約 | 内容 |
|---|---|
| [living-doc-structure](conventions/living-doc-structure.md) | 生きた文書（`overview.md` 等）の構成。見出しは名詞句のみ・連番は1始まり・履歴を本文に溜めない |
| [action-items-convention](conventions/action-items-convention.md) | `action-items.md` の運用と完了項目のアーカイブ |
| [sop-manual-authoring](conventions/sop-manual-authoring.md) | SOP・手順書・業務マニュアルの標準構成と記述作法 |
| [spreadsheet-structure](conventions/spreadsheet-structure.md) | スプレッドシート設計（入力層／表示層の分離・tidy table・権限境界でファイル分割） |
| [file-naming-and-folder-hygiene](conventions/file-naming-and-folder-hygiene.md) | 共有ファイル・フォルダの命名と配置 |
| [copy-paste-output-format](conventions/copy-paste-output-format.md) | コピペ前提テキストの出力作法（URL単独行・`.txt`/`pbcopy`） |
| [session-end-auto-file-move](conventions/session-end-auto-file-move.md) | セッション終了時のテンポラリファイル自動移動ポリシー |

配置（インストーラが行う。手動で行う場合）：

```bash
mkdir -p ~/.claude/conventions
for f in ~/claude-toolkit/conventions/*.md; do ln -sf "$f" ~/.claude/conventions/; done
```

グローバル `~/.claude/CLAUDE.md` に「作業種別ごとの規約は `~/.claude/conventions/` が正本。着手前に該当ファイルを読む」の1行を置くと、Claude が作業種別に応じて読みに行く。取り込みは任意。

## 収録スキル

**グローバル配置**（`~/.claude/skills/` へ配置し全プロジェクトで発見させる）5件：

| スキル | 内容 |
|---|---|
| [markdown-export](skills/markdown-export/SKILL.md) | markdown → Word(.docx) / PDF 変換 |
| [markdown-to-gdocs](skills/markdown-to-gdocs/SKILL.md) | markdown/docx → Google Docs アップロード＋体裁適用（要・自分のGoogle Cloud OAuthセットアップ） |
| [transcribe-meeting](skills/transcribe-meeting/SKILL.md) | 会議録音 → 議事録3点セット（原文・ケバ取り版・凝縮版）自動生成（要・Gemini APIキー） |
| [mcp-setup](skills/mcp-setup/SKILL.md) | Claude CodeへのMCPサーバー接続・セットアップ手順を案内 |
| [format-prompt](skills/format-prompt/SKILL.md) | 粗いプロンプトを7ブロックの型に整形 |

**個別プロジェクト配置**（用途が特定プロジェクトに閉じるため、グローバルには置かない）5件：

| スキル | 内容 | 配置先の目安 |
|---|---|---|
| [email-to-calendar](skills/email-to-calendar/SKILL.md) | 開催案内メールから日時・場所・URLを抽出しGoogleカレンダーへ登録。差出人別ルールを自己蓄積し継続利用で精度向上（要・`RULES.example.md`を`RULES.md`にコピー） | 秘書業務を行うプロジェクト（例：CRPC秘書業務） |
| [schedule-reply](skills/schedule-reply/SKILL.md) | 日程調整メール(tonton/調整さん)にGoogleカレンダーの空きで自動回答（要・個別セットアップ、上級者向け） | 同上 |
| [interest-profile](skills/interest-profile/SKILL.md) | 会話履歴からユーザーの興味プロファイルを生成・更新 | 利用頻度が低いプロジェクトのみ |
| [meishi-rename](skills/meishi-rename/SKILL.md) | 名刺スキャンPDFのファイル名をOCR結果から整形 | 名刺整理を行うプロジェクト |
| [person-research](skills/person-research/SKILL.md) | 人物調査URLからレジストリを横断調査し根拠付きレポートを作成 | 個人環境ラッパー（command版）から参照される汎用ロジック。単体配置は通常不要 |

## インストール

**グローバル標準セットアップ**（新Mac・新環境で最初に行う導入。以後は「claude-toolkitを導入して」で再現できる）：

```bash
git clone <このリポジトリ> ~/claude-toolkit
mkdir -p ~/.claude/skills
ln -sf ~/claude-toolkit/skills/markdown-export     ~/.claude/skills/markdown-export
ln -sf ~/claude-toolkit/skills/markdown-to-gdocs   ~/.claude/skills/markdown-to-gdocs
ln -sf ~/claude-toolkit/skills/transcribe-meeting  ~/.claude/skills/transcribe-meeting
ln -sf ~/claude-toolkit/skills/mcp-setup           ~/.claude/skills/mcp-setup
ln -sf ~/claude-toolkit/skills/format-prompt       ~/.claude/skills/format-prompt
ln -sf ~/claude-toolkit/tools/statusline/statusline.py ~/.claude/statusline.py
chmod +x ~/.claude/statusline.py
```

配置後、Claude Codeが会話の文脈（「Wordにして」「PDFにして」等）から自動的にスキルを発見する。明示的にコマンドを打つ場合は各SKILL.mdの使い方を参照。statuslineの表示には `~/.claude/settings.json` への `statusLine` キー追加が別途必要（[tools/statusline/README.md](tools/statusline/README.md)参照）。

**個別プロジェクト配置**は上記グローバル標準に含めない。該当プロジェクトの `.claude/skills/<name>/` へ個別にsymlinkする。

- `schedule-reply` は Google Calendar/Gmail への書き込み・自動送信を伴うため、配置後も `skills/schedule-reply/scripts/README.md` に沿った個別セットアップ（`config.json`作成・Google認証・Playwright）が別途必要。
- `email-to-calendar` は初回のみ `skills/email-to-calendar/RULES.example.md` を同ディレクトリの `RULES.md` にコピーして使い始める（差出人別ルールが蓄積されるファイルのため`.gitignore`済み）。

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
| [rtk](tools/rtk/README.md) | 2026-08-01に廃止。bash出力が静かに別内容へ置き換わり誤った集計を生む障害のため。2026-07-27〜08-01に導入した人は削除手順を参照 |
