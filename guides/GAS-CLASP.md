# GAS / clasp 管理

**`~/.claude/GAS-CLASP-local.md` があれば併せて読む**（無ければスキップ）。アカウント切替の実装と自分のスクリプトプロジェクト一覧はそちらにある。

## Google Spreadsheet への書き込み方針

操作規模に応じて手段を使い分けること。MCP の `write_rows` で大量データを直接送信するとトークン消費が膨大になり処理も低速になる。

| 操作規模 | 推奨手段 |
|---|---|
| 1〜5行の小修正 | `write_rows`（MCP直接） |
| 10行以上の一括書き込み | GASスクリプト or ローカルNode.js |
| シート全体の初期投入 | CSVインポート or GAS |
| QUERY式・数式の設定 | GASスクリプト（特に有効） |

**GAS（Google Apps Script）のデプロイ：** `clasp push` を使うこと（コピペ禁止）。

## clasp のアカウント切替

clasp自体はマルチアカウント非対応（`~/.clasprc.json` に1アカウントのみ保持）。**複数のGoogleアカウントでGASを使い分ける場合は、切替の仕組み（アカウントごとの認証情報を保存し、使うときに入れ替える）が要る。**

## ディレクトリ構成

各プロジェクト内の `gas/<スクリプト名>/` に配置する。

```
~/Projects/<project>/
└── <subdir>/gas/
    └── <ScriptName>/
        ├── .clasp.json       # scriptId を記録（git管理する）
        ├── appsscript.json   # タイムゾーン等の設定（git管理する）
        └── <ScriptName>.gs   # スクリプト本体（git管理する）
```

## ファイル命名規則

`.gs` ファイル名：**`<対象スプレッドシート名>_<機能>.gs`**
- 例：`MemberMaster_sync.gs`、`Form_handler.gs`
- 1スクリプトプロジェクト = 1ディレクトリ = 複数 `.gs` 可

## 認証

初回のみ・マシンごとに必要。

```bash
clasp login   # ブラウザが開く → 自分の Google アカウントでログイン
```

認証情報は `~/.clasprc.json` に保存される（git管理しない）。

## 新規プロジェクトのリンク手順

1. スプレッドシート → 拡張機能 → Apps Script → プロジェクトの設定 → スクリプトIDをコピー
2. ディレクトリ作成：
```bash
mkdir -p ~/Projects/<project>/gas/<ScriptName>
cd ~/Projects/<project>/gas/<ScriptName>
```
3. `.clasp.json` を作成：
```json
{ "scriptId": "<スクリプトID>", "rootDir": "." }
```
4. `appsscript.json` を作成：
```json
{ "timeZone": "Asia/Tokyo", "dependencies": {}, "exceptionLogging": "STACKDRIVER", "runtimeVersion": "V8" }
```

## デプロイ

毎回、変更のたびに実行する。

```bash
cd ~/Projects/<project>/gas/<ScriptName>
clasp push
```

`clasp push` はプロジェクト全体を上書きするため、個別ファイル削除は不要。

## 既存プロジェクト一覧

自分の GAS プロジェクトは `~/.claude/GAS-CLASP-local.md` に書く（ここは共有される共通部なので書かない）。

