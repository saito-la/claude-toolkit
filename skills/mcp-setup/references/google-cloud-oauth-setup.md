# Google Cloud Console での OAuth クライアント作成

Google 系 MCP サーバー（Google Drive・Google Docs・Gmail 等）に必要な OAuth クライアントIDとシークレットを取得する手順。

**すべてブラウザ作業のため、ユーザーに手順を説明して実行してもらう。** Claude が代行できる部分はない。

> **画面名は改定される。** 以下は執筆時点のラベルであり、Google Cloud Console のメニュー名・遷移は変わる（「OAuth同意画面」まわりは特に再編されやすい）。ラベルが一致しないときは、この手順の**目的**に合う画面を実際の表示から探して読み替える。逐語のラベルに固執して行き詰まらせない。

## 1. Google Cloud Console にアクセス

- URL: https://console.cloud.google.com/
- Google アカウントでログインする。

## 2. プロジェクトを作成

1. 画面上部「プロジェクトを選択」→「新しいプロジェクト」
2. プロジェクト名を入力（例: `claude-mcp`）して「作成」
3. 作成したプロジェクトが選択されていることを確認する

## 3. 使用する API を有効化

1. 左メニュー「APIとサービス」→「ライブラリ」
2. 接続するサービスの API を検索して有効化する
   - Google Drive → `Google Drive API`
   - Google Docs → `Google Docs API`
   - Gmail → `Gmail API`

## 4. OAuth 同意画面の設定

1. 左メニュー「APIとサービス」→「OAuth同意画面」
2. User Type「外部」を選択して「作成」
3. 次を入力する
   - アプリ名: `Claude MCP`（任意）
   - ユーザーサポートメール: 自分のGmailアドレス
   - デベロッパーの連絡先: 自分のGmailアドレス
4. 「保存して次へ」を繰り返して完了させ、ダッシュボードに戻る

## 5. OAuth クライアントID を作成

1. 左メニュー「APIとサービス」→「認証情報」
2. 「+ 認証情報を作成」→「OAuthクライアントID」
3. アプリケーションの種類: **「デスクトップアプリ」**を選ぶ（ここを間違えると認証フローが通らない）
4. 名前: `claude-mcp-client`（任意）
5. 「作成」→「JSONをダウンロード」
6. ダウンロードした JSON を分かりやすい場所（デスクトップ等）に保存する

保存した JSON のパスを確認できたら、SKILL.md の STEP 3（OAuth認証の実行）へ進む。
