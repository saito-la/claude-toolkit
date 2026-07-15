---
name: mcp-setup
description: Claude CodeへのMCPサーバー接続・セットアップを案内するスキル。ユーザーがMCPサーバーの追加・接続・設定をしたいとき、「MCPを使いたい」「Google DriveをClaudeに繋ぎたい」「MCPサーバーを登録したい」「NotionやGitHubをMCPで使いたい」など、MCPに関する操作を求める場合は必ずこのスキルを使うこと。
---

# MCPサーバー セットアップガイド

このスキルは、Claude CodeにMCPサーバーを接続するための手順をステップごとに案内します。
**必ずユーザーの確認を取りながら、1ステップずつ進めること。**

---

## 事前確認

ユーザーに以下を確認する：
1. 接続したいサービス名（例: Google Drive、GitHub、Notionなど）
2. MCPサーバーのパッケージ名（わからない場合はこちらで調べて提案する）

サービスに応じて以下の手順を調整すること。
Google OAuth（Google系サービス）が必要かどうかで手順が変わる。

---

## STEP 1: 環境確認

以下のコマンドを実行してNode.js・npm・npxのバージョンを確認する：

```bash
node --version && npm --version && npx --version
```

- Node.js v18以上であればOK
- 古い場合は https://nodejs.org からLTS版をインストールするよう案内する
- 確認できたらユーザーに結果を伝え、STEP 2への進行確認を取る

---

## STEP 2: 認証情報の準備

### Google系サービスの場合

対象例：Google Drive・Google Docs・Gmail など。
OAuthクライアントIDとシークレットをGoogle Cloud Consoleで作成する。
**これはブラウザ作業のため、ユーザーに以下の手順を丁寧に説明すること。**

#### 2-1. Google Cloud Consoleにアクセス
- URL: https://console.cloud.google.com/
- Googleアカウントでログイン

#### 2-2. プロジェクトを作成
1. 画面上部「プロジェクトを選択」→「新しいプロジェクト」
2. プロジェクト名を入力（例: `claude-mcp`）して「作成」
3. 作成したプロジェクトが選択されていることを確認

#### 2-3. 使用するAPIを有効化
1. 左メニュー「APIとサービス」→「ライブラリ」
2. 接続するサービスのAPIを検索して有効化
   - Google Drive → `Google Drive API`
   - Google Docs → `Google Docs API`
   - Gmail → `Gmail API`

#### 2-4. OAuth同意画面の設定
1. 左メニュー「APIとサービス」→「OAuth同意画面」
2. User Type「外部」を選択して「作成」
3. 以下を入力：
   - アプリ名: `Claude MCP`（任意）
   - ユーザーサポートメール: 自分のGmailアドレス
   - デベロッパーの連絡先: 自分のGmailアドレス
4. 「保存して次へ」を3回クリック→「ダッシュボードに戻る」

#### 2-5. OAuthクライアントID作成
1. 左メニュー「APIとサービス」→「認証情報」
2. 「+ 認証情報を作成」→「OAuthクライアントID」
3. アプリケーションの種類: **「デスクトップアプリ」**を選択
4. 名前: `claude-mcp-client`（任意）
5. 「作成」→「JSONをダウンロード」
6. ダウンロードしたJSONファイルをデスクトップなどわかりやすい場所に保存

完了したらユーザーに確認し、STEP 3へ。

### Google以外のサービスの場合

サービスのAPIドキュメントに従ってAPIキーやトークンを取得するよう案内する。
取得方法はサービスごとに異なるため、ユーザーと一緒に調べながら進めること。

---

## STEP 3: OAuth認証の実行

Google系サービスのみ対象。Google以外でAPIキー方式の場合はこのSTEPをスキップ。

#### 3-1. 認証用ファイルを所定の場所にコピー

`@modelcontextprotocol/server-gdrive` は `~/gcp-oauth.keys.json` という名前のファイルを期待する：

```bash
cp "<ダウンロードしたJSONのフルパス>" ~/gcp-oauth.keys.json
```

ユーザーにファイル名を確認してからコマンドを提示すること。

#### 3-2. 認証コマンドを実行

ユーザーに**新しいターミナルウィンドウを開いて**以下を実行してもらう：

```bash
npx @modelcontextprotocol/server-gdrive auth
```

- ブラウザが開いてGoogleのログイン・許可画面が表示される
- Googleアカウントでログインして「許可」をクリック
- ターミナルに `Credentials saved. You can now run the server.` が表示されれば成功

エラーが出た場合は内容をそのまま共有してもらい、一緒に解決する。

完了したらSTEP 4へ。

---

## STEP 4: Claude CodeへのMCPサーバー登録

以下のコマンドを実行する（サービスに応じて調整）：

### Google Drive の場合

```bash
claude mcp remove gdrive 2>/dev/null; claude mcp add gdrive -e GDRIVE_CREDENTIALS_PATH=/Users/$USER/.gdrive-server-credentials.json -- npx -y @modelcontextprotocol/server-gdrive
```

### その他のサービスの場合

```bash
claude mcp add <サーバー名> -- npx -y <パッケージ名>
```

APIキーが必要な場合は `-e API_KEY=xxx` オプションで環境変数を渡す。

登録後、設定を確認する：

```bash
claude mcp list
```

サーバーが一覧に表示されればOK。

完了したらユーザーにClaude Codeの**再起動**を依頼し、STEP 5へ。

---

## STEP 5: 動作確認

Claude Codeを再起動後、ユーザーに以下のように試してもらう：

### Google Drive の場合
- 「Googleドライブのファイルを一覧表示して」
- 「Googleドライブで〇〇というファイルを検索して」

### その他のサービスの場合
- そのサービスで基本的な操作（取得・検索など）を試してもらう

MCPのツールが応答すれば成功。エラーが出た場合はメッセージを共有してもらい解決する。

---

## よくあるエラーと対処法

### `Cannot find module 'gcp-oauth.keys.json'`
- `~/gcp-oauth.keys.json` が存在しない
- STEP 3-1のコピー手順を再実行する

### `Credentials not found`
- `~/.gdrive-server-credentials.json` が存在しない
- STEP 3の認証を再実行する

### `MCP server already exists`
- 同名のサーバーが既に登録されている
- `claude mcp remove <サーバー名>` で削除してから再登録する

### ブラウザが開かない
- ターミナルでコマンドを実行中に表示されるURLを手動でブラウザに貼り付ける

---

## 注意事項

- Client IDやClient Secretなどの秘密情報はチャットに貼り付けない
- 認証情報ファイル（JSONや `gcp-oauth.keys.json`）はGitにコミットしない
- MCPサーバーはプロジェクト単位で登録される（`~/.claude.json` に保存）
