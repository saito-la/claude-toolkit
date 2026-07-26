# MCP セットアップのつまずき

## 認証情報が見つからない

- `Cannot find module 'gcp-oauth.keys.json'` 等：MCP サーバーが期待するパスにクライアントJSONが無い。パッケージの README で期待パスを確認して配置し直す。
- `Credentials not found` 等：トークンが未生成。そのサーバーの認証コマンドを再実行する。

## 登録に失敗する

- `MCP server already exists`：同名が登録済み。`claude mcp remove <サーバー名>` で削除してから再登録する。
- 登録できたのにツールが現れない：Claude Code の再起動を忘れている。またはスコープ違い（プロジェクト単位で登録したものを別プロジェクトで使おうとしている）。`claude mcp list` を該当プロジェクトで実行して確認する。

## ブラウザが開かない

ターミナルに表示される URL を手動でブラウザに貼り付ける。SSH 越しの実行では常にこの方法になる。

## 参考実装

`@modelcontextprotocol/server-gdrive` を使う場合の具体形（サーバーによってパス・環境変数名は変わる）：

```bash
cp "<ダウンロードしたJSONのパス>" ~/gcp-oauth.keys.json
npx @modelcontextprotocol/server-gdrive auth       # → Credentials saved. で成功
claude mcp add gdrive -s user \
  -e GDRIVE_CREDENTIALS_PATH="$HOME/.gdrive-server-credentials.json" \
  -- npx -y @modelcontextprotocol/server-gdrive
```
