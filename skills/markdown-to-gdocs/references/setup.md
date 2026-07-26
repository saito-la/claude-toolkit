# markdown-to-gdocs セットアップ

自分の Google Cloud プロジェクトが必要。

1. Google Cloud Console で新規プロジェクトを作成し、OAuth同意画面・OAuthクライアント（**デスクトップアプリ**）を設定する。
2. Google Drive API・Google Docs API を有効化する。
3. ダウンロードしたクライアントJSONを `~/.config/gdrive-mcp/credentials.json` に配置する。
4. `upload-gdoc.mjs` を初回実行するとブラウザが開くので、使いたいGoogleアカウントでログイン → 「未確認アプリ」は「詳細」→「移動」→許可。認証情報は `~/.config/gdrive-mcp/.gdrive-credentials-<account>-rw.json` に保存される。
5. 本番公開済みOAuthに昇格しておくと、以後は自動更新で再認証不要になる（任意）。
