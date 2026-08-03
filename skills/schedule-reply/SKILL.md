---
name: schedule-reply
description: tonton／調整さんの日程調整入力依頼メールに、カレンダーの空き（config.availabilityで設定した営業時間）でconfig.displayNameの名義で自動回答するスキル。Gmailのinboxを検知し、空き判定→自動入力・送信→送信後にブラウザで確認→処理済みメールを Scheduling ラベルへ移動＋既読化する。「日程調整メールに回答して」「tonton/調整さんに回答して」「スケジューラを回して」「日程調整の自動回答をして」等でトリガーする。実体は scripts/poll.mjs の Node CLI（このスキルは呼び出し口）。要個別セットアップ（config.json作成・Google認証・Playwright）。
---

# schedule-reply — 日程調整メール自動回答

tonton／調整さんの日程調整入力依頼メールを検知し、カレンダーの空きを config.availability で設定した営業時間で判定して config.displayName の名義で自動回答する。**このSKILL.mdが置かれているディレクトリを基準に、`scripts/poll.mjs` を実行すること。**symlink経由で読み込まれていても、実体のディレクトリを解決してから使う。仕様の正本は `scripts/README.md`。

**利用には個別セットアップが必須**（自分の `config.json` 作成・Google OAuth・Playwright）。詳細は `scripts/README.md` の「セットアップ」参照。

## 用途

- ユーザーが「日程調整メールに回答して」「tonton/調整さんに回答して」「スケジューラを回して」等と言ったとき（inbox内の未処理案件を一括処理）。
- ユーザーが特定の1通（Message-ID・件名・URL等で指定）だけへの回答を求めたとき（下記「単発指定」参照）。
- 送信せず判定だけ見たいときは dry-run で実行する。

## 実行手順

1. スケジューラを実行する。
   - 送信あり・一括（既定）：`node <このSKILL.mdのディレクトリ>/scripts/poll.mjs --send --verbose`
   - 判定のみ（安全確認）：`node <このSKILL.mdのディレクトリ>/scripts/poll.mjs --dry-run --verbose`
   - **単発指定**（ユーザーが特定の1通だけを指定したとき、必須）：まず gmail 検索（Message-ID指定なら `rfc822msgid:<Message-ID>`）でGmail内部IDを特定し、`node <このSKILL.mdのディレクトリ>/scripts/poll.mjs --send --verbose --only <GmailメッセージID>` を実行する。**これを省略すると inbox 内の他の未処理案件（別件のtonton/調整さん）も一括で自動送信されてしまう**（実例：2026-08-03、1件だけ依頼されたのに inbox に溜まっていた他2件も巻き込みで送信された）。
2. 標準出力の要約（ツール・候補数・件名・候補ごとの ◯/△/✕ と根拠・スキップ理由）をそのままユーザーへ報告する。
3. 送信した場合、CLI が回答ページを既定ブラウザで開く。ユーザーに内容確認を促す。
4. 処理済みメールは自動で inbox→「Scheduling」へ移動・既読化される（`gmail.modify` 付与済み。未付与時はスキップ警告が出る）。

## 出力の解釈と報告

- 各候補の判定（空き＝◯、一部＝△、不可/時間外/週末＝✕）と根拠を表で伝える。
- スキップ・除外（config.exclude で設定した送信者・件名パターン／引用のみ／議事録・通知）はログ理由を添えて報告する。
- エラー時は該当メールを飛ばして次回再試行される（`state.jsonl` で二重回答は防止）。

## 前提条件

- `~/.config/schedule-reply/config.json` が存在（無ければ README「セットアップ」に従って `scripts/config.example.json` から作る）。既定 `flags.dryRun=false`＝送信あり。
- Playwright がローカル解決可能（`scripts/node_modules/playwright`。無ければ README のフォールバックで symlink）。ブラウザ本体が無い場合は `npx playwright install chromium`。
- カレンダー・Gmailのトークンが有効。切れたら認証設定に従い再認証。

## コードと個人データの分離

- **コード** — この skill ディレクトリ（git 管理）。
- **個人データ** — `~/.config/schedule-reply/` に `config.json`・`state.jsonl`（処理済み記録）・`logs/`。

`state.jsonl` は「どのメールに回答済みか」の記録で、**消えると同じ日程調整に二重回答する**。skill を配置し直すとき・別マシンへ移すときは、リポジトリではなく `~/.config/schedule-reply/` を持っていく。

## 注意

- 実在ポーリング（メール差出人に見える）への書き込みを伴う。誤検知が疑わしいときは先に `--dry-run` で確認する。
- tonton は同名＋パスワード（`config.browser.tontonEditPassword`）で既存回答を削除して上書きするため再実行しても重複しない。
- 誤って送信した回答は取り消せる。tonton は回答ページで自分の名前の削除リンク→編集パスワード入力。調整さんは自分の名前をクリック→「登録を削除」。
- 無人自動巡回は launchd（`scripts/schedule-reply.plist.example`）で別途設定。本スキルは会話経由の都度実行用。

## 参照

- `scripts/README.md` — 仕様・判定ロジック・セットアップ・本番投入
