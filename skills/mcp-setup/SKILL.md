---
name: mcp-setup
description: Claude Code に MCP サーバーを接続・設定するときに使うスキル（「MCPを使いたい」「Google DriveをClaudeに繋ぎたい」等）。
---

# MCPサーバー セットアップガイド

Claude Code に MCP サーバーを接続する。**ブラウザ作業とターミナル作業が交互に来るので、ユーザーの手が必要な箇所では止まって結果を待つこと。**

## 事前に決めること

- 接続したいサービス（Google Drive・GitHub・Notion 等）
- 使う MCP サーバーのパッケージ名（不明ならこちらで調べて提案する）
- 認証方式：**Google OAuth 系**か**APIキー・トークン系**か。以降の手順がここで分岐する。

## Google OAuth 系

対象は Drive・Docs・Gmail・Calendar 等。Google Cloud Console でクライアントを作る。達成すべきことは5つ。

1. プロジェクトを作成する（例: `claude-mcp`）
2. 接続するサービスの API を有効化する（Drive → `Google Drive API`、Docs → `Google Docs API`、Gmail → `Gmail API`）
3. OAuth 同意画面を設定する（User Type「外部」、アプリ名と連絡先メール）
4. OAuth クライアントIDを作成する。種類は必ず **「デスクトップアプリ」**（ここを間違えると認証フローが通らない）
5. クライアントJSON をダウンロードして保存する

**画面ごとのクリック手順は `references/google-cloud-oauth-setup.md`。**画面名・遷移は Google が改定するため、逐語のラベルが一致しないときは目的に合う画面を実際の表示から探して読み替える。

続いて、使う MCP サーバーが期待する場所へクライアントJSONを置き、そのサーバーの認証コマンドを実行してトークンを生成する（期待するパス・コマンド名はサーバーごとに異なるので、パッケージの README を確認する）。ブラウザが開くのでユーザーにログイン・許可してもらう。**「未確認アプリ」警告が出るのは自作クライアントでは正常**で、「詳細」→「移動」で進める。

## APIキー・トークン系

サービスの API ドキュメントに従ってキーを発行してもらう。取得方法はサービスごとに異なるので、ユーザーと一緒に調べながら進める。

## 登録

```bash
claude mcp add <サーバー名> -- npx -y <パッケージ名>
claude mcp add <サーバー名> -s user -e API_KEY=xxx -- npx -y <パッケージ名>   # 環境変数を渡す／全プロジェクトで有効にする
claude mcp list
```

- スコープ既定はプロジェクト単位。**複数プロジェクトから使うものは `-s user`** で登録する（`~/.claude.json` に保存される）。
- 同名が既にあると失敗するので、入れ替えるときは `claude mcp remove <サーバー名>` を先に実行する。
- 登録後は Claude Code の**再起動**が必要。再起動後にそのサービスで検索・取得を試して応答を確認する。

## つまずいたとき

`references/troubleshooting.md`。
