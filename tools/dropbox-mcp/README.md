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

初回のみ、MCP 登録後にそのセッションから次の2ツールを順に呼ぶ。

1. `dropbox_auth_get_url` — 認可URLを取得（`openBrowser: true` でこの端末のブラウザを自動で開く）
2. ブラウザで対象アカウントとして認可し、表示された code をコピー
3. `dropbox_auth_exchange_code`（`authCode: <code>`）— refresh token を取得し、既定で `DROPBOX_TOKEN_FILE` へ保存する

以後はそのトークンファイルが使われ、再認可は不要（トークンファイルを新規端末へコピーする場合も再認可は要らない）。

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
