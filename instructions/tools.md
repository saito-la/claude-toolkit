## Box ファイルへのアクセス

Box URL（`*.app.box.com/file/<ID>`）が提示されたら **WebFetch は使わず Box CLI（`box files:get` / `box files:download`）を使う**。WebFetch は OAuth2 認証を通れずログインページにリダイレクトされて失敗する。docx 等はダウンロード後に `pandoc -t markdown` で読む。

## GAS / clasp

GAS・clasp・Apps Script への言及時、または `gas/` ディレクトリ操作時は `~/.claude/GAS-CLASP.md` を読むこと（Spreadsheet 書き込み方針を含む）。

## Google Sheets 関数

Google Sheets の数式（QUERY・ARRAYFORMULA・LAMBDA等）作成・デバッグ時は、既知の落とし穴があれば自分の環境ドキュメント（無ければ適宜作成）を確認する。新しい落とし穴・パターンを解決したら追記して蓄積する。

## Google Calendar イベント作成

gcal-* MCP で `location` を設定するときは裸のURLのみを入れる。「Zoom: URL」等のラベルを前置するとGoogle Calendar上でクリックできなくなる。

## モデル使用方針

既定は **Opus 5**（`settings.json` の `"model": "opus"`）。大量ファイルの機械的な一括処理や、レート制限を抑えたいと言われたときは **Sonnet 5** への切り替えを提案してよい（切り替えは `/model` でユーザーが手動で行う）。

## セッション終了

「セッションを終わります」「終えます」「終了します」等の発言を検知したら `~/.claude/SESSION-END.md` を読んで実行すること。
