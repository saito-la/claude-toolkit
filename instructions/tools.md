## Box ファイルへのアクセス

Box URL（`*.app.box.com/file/<ID>`）が提示されたら **WebFetch は使わず Box CLI（`box files:get` / `box files:download`）を使う**。WebFetch は OAuth2 認証を通れずログインページにリダイレクトされて失敗する。docx 等はダウンロード後に `pandoc -t markdown` で読む。

## 探索コマンドの実行証拠

**探索を `timeout` で包まない。** macOS に `timeout` は無く、GNU coreutils を入れても入るのは `gtimeout` である。`timeout 30 grep -ril ... 2>/dev/null` と書くとコマンドは実行されないまま空の出力を返し、`2>/dev/null` が `command not found` を捨てるため、**0件のヒットと区別が付かない**。実際にこれで「記載なし」と誤報告した（2026-08-16）。

**「無い」「ヒットしない」と報告する前に、その検索が実行された肯定的証拠を取る。** 終了コード、`grep -c` の件数、走査対象のファイル数など。ラッパーの不在は本体の不在と違い、コマンドの意味を変えずに実行そのものを消すため、空の出力は0件の証拠にならない。

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
