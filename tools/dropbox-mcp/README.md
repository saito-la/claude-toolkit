作成日：2026-09-07
改訂日：2026-09-13

## これは何か

`dropbox-personal` 等の Dropbox MCP サーバーの自前実装。npm パッケージ `@fm-phibia/dropbox-mcp`（個人のメンテなしプロジェクト、公開は2025-12-15の1バージョンのみ）を置き換える。原因調査は元は `ai-environment/docs/mcp/20260907-dropbox-mcp-binary-corruption.md`（俊樹本人環境。CRPCメンバーは参照不可）にあった。**2026-09-13、正本をこのリポジトリへ一本化した**（実装そのものはアカウントに依存しないため、CRPC等の別アカウントでもこのコードをそのまま使い回せる）。

## 何が壊れていたか

`@fm-phibia/dropbox-mcp` は HTTPS レスポンスを種類を問わず `Buffer.concat(chunks).toString()`（既定UTF-8）でデコードしていた。`/2/files/download` の応答本体（バイナリ）もここを通るため、非UTF-8バイト列が不可逆に置換文字（U+FFFD）へ壊れていた。加えてツールハンドラは結果を常に MCP の `type: "text"` として返す設計で、そもそもバイナリを安全に運ぶ経路自体が無かった。

## 設計方針

- **ダウンロードはローカルディスクへ直接ストリーム保存し、会話コンテキストには保存先パス・サイズ・sha256 だけを返す。** バイナリを base64 化して tool result に載せる案もあるが、それは壊れなくなるだけでトークンを浪費する（数百KBのファイルが数十万トークンになりうる）。「ファイル内容をモデルの会話コンテキストに載せない」のがトークン効率と正しさの両方を満たす唯一の解。
- **アップロードはローカルファイルパスを受け取りバイト列のまま POST する。** `content` を文字列で受け取る入力経路自体を廃止し、同じ壊れ方が起きる余地を無くした。
- JSON を返すエンドポイント（トークン交換・list_folder・upload結果）だけを文字列/JSONとしてデコードする。バイナリ経路（`downloadToFile`）とは関数を完全に分離してある。
- **アカウントはすべて環境変数で渡す。** コード自体に特定アカウントのApp key/secret・トークンファイルパスをハードコードしていない。同じ `index.mjs` を、Dropbox App・トークンファイルの組だけ変えて何人でも・何アカウントでも登録できる。

## セットアップ

新規マシンごとに1回実行する。

```bash
cd ~/Projects/claude-toolkit/tools/dropbox-mcp
npm install --omit=dev
```

**claude-toolkit の置き場は端末で2形式ある。** 上は直 clone した端末のパスで、**配布パッケージ方式の端末では `~/Projects/saito-la/claude/vendor/claude-toolkit/tools/dropbox-mcp`** になる（判定は `akiko-office` の `docs/bootstrap.md`）。以下の `npm install` と `claude mcp add` のパスは、その端末の実際の配置へ読み替えること。

### Dropbox App の準備

アカウントごとに1回、対象の Dropbox アカウントでログインした状態で https://www.dropbox.com/developers/apps を開き、App を新規作成して **App key** と **App secret** を発行する。発行したキーは 1Password 等の秘匿ストアへ保存し、リポジトリ・チャット・メールには置かない（`secret-handling.md` の作業規約に従う）。

### MCP 登録

**MCP サーバー名・トークンファイルはアカウントごとに変える。** 同一端末で複数アカウントを使う場合、`DROPBOX_TOKEN_FILE` を共有すると片方のトークンで上書きされる。

```bash
claude mcp add dropbox-<用途や本人を示すラベル> \
  --scope user \
  -e DROPBOX_APP_KEY=<App_key> \
  -e DROPBOX_APP_SECRET=<App_secret> \
  -e DROPBOX_TOKEN_FILE=<ホームディレクトリ>/.dropbox_token_<ラベル> \
  -- node ~/Projects/claude-toolkit/tools/dropbox-mcp/index.mjs
```

`DROPBOX_TOKEN_FILE` を省略すると既定値 `~/.dropbox_token` になる（1台に1アカウントしか登録しないなら省略してよい）。

### 認可

初回のみ、次の2ツールを順に呼ぶ。**ただし MCP ツールは登録した当のセッションからは呼べない**（反映は次のセッションから）ので、登録した直後に済ませたいなら下の「セッションを開き直さずに認可する」を使う。

1. `dropbox_auth_get_url` — 認可URLを取得（`openBrowser: true` でこの端末のブラウザを自動で開く）
2. ブラウザで対象アカウントとして認可し、表示された code をコピー
3. `dropbox_auth_exchange_code`（`authCode: <code>`）— refresh token を取得し、既定で `DROPBOX_TOKEN_FILE` へ保存する

以後はそのトークンファイルが使われ、再認可は不要（トークンファイルを新規端末へコピーする場合も再認可は要らない）。

### セッションを開き直さずに認可する

登録した直後のセッションで済ませたいときは、Dropbox の OAuth を直接叩く。やることは上の3手順と同じで、MCP を経由しないだけである。

認可 URL は次の形。**`token_access_type=offline` を落とすと refresh token が返らず、アクセストークンの期限切れごとに認可し直すことになる。**

```
https://www.dropbox.com/oauth2/authorize?client_id=<App_key>&response_type=code&token_access_type=offline
```

ブラウザで対象アカウントとして認可すると code が表示されるので、`https://api.dropboxapi.com/oauth2/token` へ `grant_type=authorization_code` と `code` を POST する（認証は App key/secret の Basic）。返る `refresh_token` を `{"refresh_token": "..."}` の形で `DROPBOX_TOKEN_FILE` へ書き、権限を 600 にする。

**応答の `scope` を確認する。** 読み取りだけで足りる用途なら `account_info.read files.content.read files.metadata.read` になっているはず。書き込み権限が入っていたら、App の Permissions を絞って認可し直す。

## 動作確認

```bash
DROPBOX_APP_KEY=<App_key> DROPBOX_APP_SECRET=<App_secret> DROPBOX_TOKEN_FILE=<トークンファイル> node test-manual.mjs [/dropbox/上のファイルパス]
```

ファイルパスを渡すと一時ディレクトリへダウンロードし、`dropbox_list_folder` の `size` と突き合わせてバイト単位で壊れていないか確認できる。App key/secret はコマンドライン引数に直書きせず、環境変数越しに渡すこと（シェル履歴・プロセス一覧に残さないため）。

## ツール

`dropbox_list_folder`・`dropbox_download`・`dropbox_upload`・`dropbox_auth_status`・`dropbox_auth_get_url`・`dropbox_auth_exchange_code`。旧パッケージにあった `dropbox_generate_filename` は使用実績が無く廃止した。

`dropbox_download` は `filePath`（Dropbox側パス）と任意の `destPath`（ローカル保存先。省略時は `~/Downloads/dropbox-mcp/<basename>`）を取る。`dropbox_upload` は `filePath`（Dropbox側の保存先）と `srcPath`（ローカルの元ファイル）を取る——旧パッケージの `content` 文字列入力は廃止した。

## 導入実績

- 俊樹本人（`dropbox-personal`、App `tosh-claude-mcp`）: okra・tesla・iPhoneAir（2026-09-07）
- akiko-office（`amoriya-tky@umin.ac.jp` 用に導入検討中。詳細は `akiko-office` の `action-items.md`「Dropbox MCP 導入」）
