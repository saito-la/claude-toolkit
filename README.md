# claude-toolkit

Claude Code用の汎用Skill集。個人情報・秘密情報を一切含まない、プロジェクト非依存のツール群。

[saito-la](https://github.com/saito-la) と [utokyo-crpc](https://github.com/utokyo-crpc) の両方に同一内容を配布している（1つのローカルリポジトリから両方のGitHub Organizationへ同時にpushする運用）。

## 収録スキル

| スキル | 内容 |
|---|---|
| [markdown-export](skills/markdown-export/SKILL.md) | markdown → Word(.docx) / PDF 変換 |
| [markdown-to-gdocs](skills/markdown-to-gdocs/SKILL.md) | markdown/docx → Google Docs アップロード＋体裁適用（要・自分のGoogle Cloud OAuthセットアップ） |

## インストール

各スキルディレクトリを `~/.claude/skills/<name>/` へ配置する（symlinkでもコピーでも可）。

```bash
git clone <このリポジトリ> ~/claude-toolkit
mkdir -p ~/.claude/skills
ln -sf ~/claude-toolkit/skills/markdown-export   ~/.claude/skills/markdown-export
ln -sf ~/claude-toolkit/skills/markdown-to-gdocs ~/.claude/skills/markdown-to-gdocs
```

配置後、Claude Codeが会話の文脈（「Wordにして」「PDFにして」等）から自動的にスキルを発見する。明示的にコマンドを打つ場合は各SKILL.mdの使い方を参照。

## 依存関係

- `markdown-export`：pandoc, python-docx, lxml, pymupdf, Google Chrome（PDF生成）
- `markdown-to-gdocs`：Node.js, 自分のGoogle Cloud OAuthクライアント（詳細はSKILL.md参照）

## メンテナンス方針

このリポジトリはsaito-la・utokyo-crpc双方に共通する汎用ツールのみを置く場所。特定の家族・組織にしか関係ない内容（個人設定テンプレート、組織固有の業務ツール等）はここに置かない。
