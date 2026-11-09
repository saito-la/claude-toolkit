# assistant

作成日：2026-10-24
改訂日：2026-11-10

## 1. 概要

### 1.1. 何をするものか

秘書の仕組みのエンジン。各プロジェクトの台帳（`action-items.md`）・作業用の文書（Google ドキュメント）・予定表・Gmail を読み、その日にやることを優先順位つきで並べた today.md を作る。前日分を記録用の文書へ移し、2週間の予定を作業用の文書へ転記する。

1人の使い手の環境で育てた仕組みを、ほかの使い手でも動かすために 2026-10-24 に切り出し、2026-11-10 に claude-toolkit へ移した。

### 1.2. 文書

- 決まり（何を読み、どう並べ、何を書き換えてよいか）：[docs/design.md](docs/design.md)
- 朝の一式（init）と日中の更新（update）の手順：[docs/daily-and-update.md](docs/daily-and-update.md)
- 呼び出し口：[SKILL.md](SKILL.md)
- 設定の項目：この README の「4. 設定」

### 1.3. 前提

- 台帳の書き方は claude-toolkit の [conventions/action-items-convention.md](../../conventions/action-items-convention.md)。返事待ち・期日・目安の書式はここが正本で、エンジンはそれを読む
- 閉じた行の退避は `archive-action-items.py`（設定の `archiveScript`）

## 2. エンジンとインスタンス

### 2.1. 置き場の分け方

- エンジン（このフォルダ）：コードと手順書だけを持つ。claude-toolkit の `install.py` が `~/.claude/skills/assistant` に置く。自分のフォルダには何も書かない
- インスタンス：使い手ごとのフォルダ。設定・出力・実行の記録を持つ。git のリポジトリにして、使い手の端末どうしで同期してよい

インスタンスに置くもの。

- `assistant.json`：設定（4章）。これがあるフォルダをインスタンスとみなす
- `today.md`：出力。git に入れるかはインスタンスが決める（人事など見せられない件名が混ざるなら入れない）
- `data/`：収集結果と点検の控え（`candidates.json`・`evidence.json`・`today-refs.json`・`run-costs.csv`）。`.gitignore` に入れる
- `top-priority.md`：使い手が指定した最優先課題（任意）
- `keep-in-mind.md`：意識しておく案件（任意。`docs/design.md`「意識しておく案件」）
- `routine.json`：生活パターン（任意。無ければ 06:00〜23:00 を空きとする）
- `conflict-rules.json`：予定の重なりの判定の設定（任意。無ければ重なりを見ない）。項目は `scripts/conflicts.mjs` の冒頭
- `escalation.csv`：督促で宛先を足す規則（任意）
- `attendance-notices.csv`：勤務先への連絡のうち、メールに残らないもの（チャット・口頭）を済ませた記録（`notice-done.mjs` が書く。`attendanceNotices` を使うときだけ）
- `docs/migration-log.md`：作業用の文書から台帳へ移した記録（`doc-migrate.mjs` が書く）
- `overview.md`：その使い手に固有の決まり（任意。`design.md` と併せて読む）
- `.claude/settings.json`：開始時の収集のフック（2.2）

### 2.2. 開始時の収集のフック

インスタンスの `.claude/settings.json` に次を置く。セッションを開くたびに候補を集め、要約を文脈に渡す。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "PATH=\"/opt/homebrew/bin:/usr/local/bin:$PATH\"; f=\"$HOME/.claude/skills/assistant/scripts/session-start.mjs\"; if [ -f \"$f\" ]; then node \"$f\"; else echo \"秘書の仕組みのエンジンがこの端末に無い。claude-toolkit の install.py を流してから開き直す\"; fi",
            "timeout": 90
          }
        ]
      }
    ]
  }
}
```

### 2.3. スクリプトの呼び方

インスタンスのフォルダを作業ディレクトリにして `node ~/.claude/skills/assistant/scripts/<名前>.mjs` で呼ぶ。スクリプトは作業ディレクトリから上へたどって `assistant.json` を探す。別の場所から呼ぶときは環境変数 `ASSISTANT_INSTANCE` にインスタンスのフォルダを入れる。

- `collect.mjs`：候補を集めて `data/candidates.json` に書く
- `session-start.mjs`：開始時のフックの本体（Apple Notes の取り込みと `collect.mjs`）
- `evidence.mjs`：前回の today.md の各項目に、その後に起きたことの印を付ける
- `check-today.mjs`：today.md の各項目が済んでいないか、台帳の参照がずれていないか、書き方（1 action 80文字以内・他人ボールの節なし等。`docs/design.md`「今日の一覧の書き方」）に沿うかを点検する
- `check-names.mjs`：today.md の人名の漢字が一次ソースにあるかを点検する（`features.checkNames`）
- `close-items.mjs`：済んだ台帳の行を閉じて退避し、commit・push する
- `linkify-today.mjs`・`dispatch.mjs`：pj リンクと作業の振り分け（`features.pjLinks`）
- `doc-2w.mjs`：予定表の2週間分を作業用の文書へ転記する。締切のタスクは各日の先頭に入れる
- `deadline-tasks.mjs`：today.md の締切の節を Google Tasks のタスクにする（`deadlineTasks`）
- `doc-log.mjs`：作業用の文書の前日分を記録用の文書へ移す
- `doc-done.mjs`：やったことを作業用の文書へ書く（`features.done`）
- `doc-remove.mjs`・`doc-migrate.mjs`：作業用の文書の行を台帳へ移し、文書から消す
- `notes-to-doc.mjs`・`notes-remove.mjs`：Apple Notes の捕捉用のノートを作業用の文書へ移す（`features.appleNotes`）
- `notice-done.mjs`：勤務先への連絡をチャットや口頭で済ませた日を `attendance-notices.csv` に残す
- `auth-google.mjs`：Google の資格情報を取る（3章）
- `ledgers.mjs`：台帳を探し、返事待ちの行を読む。インスタンスの外の道具（受信メールの自動処理など）も import するので、設定を読まない。関数の形を変えるときは、import している道具も直す

## 3. 導入

### 3.1. エンジン

claude-toolkit を clone して `install.py` を流す（claude-toolkit の README「インストール」）。`~/.claude/skills/assistant` にエンジンが置かれる。

- 更新は claude-toolkit の pull で届く。symlink で置く端末（Mac・Linux）は pull だけで効き、コピーで置く端末（Windows）は `install.py` の再実行が要る
- 配布パッケージの submodule として claude-toolkit を持つ端末では、submodule の参照先が進んだときに届く

### 3.2. 資格情報

Google の API を読む資格情報は、設定の `credentialDir` のフォルダに置く。

1. OAuth のクライアントを用意する。種類は「デスクトップ アプリ」。作り方は claude-toolkit の [skills/mcp-setup/references/google-cloud-oauth-setup.md](../mcp-setup/references/google-cloud-oauth-setup.md)。組織で1つ作って配ってもよい。ダウンロードした JSON を `credentialDir` に `oauthClient` の名前（既定 `credentials.json`）で置く
2. インスタンスのフォルダで、アカウントごとに資格情報を取る。権限は使う機能に合わせて並べる

```bash
node ~/.claude/skills/assistant/scripts/auth-google.mjs .google-work.json documents calendar.readonly gmail.readonly --hint taro@example.com
```

3. 取ったファイルの名前を `assistant.json` の各項目の `credential` に書く

使う権限の目安：作業用の文書・記録用の文書は `documents`、予定表は `calendar.readonly`、Gmail は `gmail.readonly`、締切のタスクは `tasks`。資格情報のファイルは git に入れない。

### 3.3. インスタンス

1. フォルダを作り、`assistant.json` を置く（4章）
2. `.claude/settings.json` に開始時のフックを置く（2.2）
3. 資格情報を取る（3.2）
4. そのフォルダで claude を開き、「init」と言う。初回は前回の today.md が無いので、台帳の未処理の行をすべて1件ずつ確かめ、時間がかかる

## 4. 設定

### 4.1. 書き方

インスタンスの `assistant.json` に書く。既定値の正本は `scripts/lib/instance.mjs` の `DEFAULTS`。1段目の項目がオブジェクトなら、書いた鍵だけが既定値を上書きする。

項目を足すときは、今の設定のままで動く既定値を `DEFAULTS` に足す。使い手が設定を直さなくても改良が届くようにするため。既定値を置けない項目（文書の ID など、他人の文書を書き換えかねないもの）は、使うスクリプトが起動時に項目の名前を出して止まる。

### 4.2. 項目

- `name`（既定 インスタンスのフォルダの名前）：インスタンスの呼び名。依頼文・開始時の文脈・台帳を閉じたときの記録とコミットに使う。worktree で動いてもフォルダの名前に引きずられないよう書いておく
- `workDoc`（必須）：作業用の文書。`id`（Google ドキュメントの ID）、`credential`（書き込める資格情報のファイル名）、`name`（呼び名。メッセージと見出しに使う。既定「作業用の文書」）
- `logDoc`（`doc-log.mjs` を使うなら必須）：記録用の文書。`id`・`credential`・`name`（既定「Log」）
- `calendars`（既定 空）：予定表の並び。各要素は `label`（表示名）、`credential`（calendar.readonly を持つ資格情報）、`calendarId`（既定 `primary`）。複数あれば1つの並びにまとめる
  - Google の API で読めない予定表は、別の道具が書き出したファイルで渡せる。要素を `type: "file"`・`path`（インスタンスからの相対か `~/` で始まる絶対）・`maxAgeHours`（既定 24。これより古いと警告）にする。ファイルの形と、無いときに飛ばす扱いは `scripts/lib/calendars.mjs` の冒頭。Outlook の予定表などに使う
- `workplaceTitles`（既定 空）：予定ではなく勤務場所の記載として扱う予定の題名（完全一致）。「`<拠点> (オフィス)`」の形は挙げなくても勤務場所として扱う（`docs/design.md`「空き時間の判定」）
- `mailFiles`（既定 空）：Gmail の API で読めないメールボックスを、別の道具が書き出したファイルで渡す。各要素は `label`・`path`（インスタンスからの相対か `~/` で始まる絶対）・`maxAgeHours`（既定 24。これより古いと警告）。ファイルの形は `scripts/lib/mail-files.mjs` の冒頭。`collect.mjs` は `candidates.json` の `mailFiles` に入れるだけで、返事待ちの照合・`evidence.mjs`・`check-today.mjs` は Gmail だけを見る。Outlook のメールなどに使う
- `gmail`（既定 なし）：Gmail。`account`（リンクの authuser に入れるアドレス）、`credential`、`sentExcludeSubjects`（送信済みから除く自動通知の件名の並び）。無ければ inbox・送信済み・返事待ちの返事を見ない
- `ledgers.roots`（既定 `["."]`）：台帳を探す場所。`~/Projects` からの相対で、`"."` は全体
- `ledgers.exclude`（既定 空）：`roots` の下でも読まない場所。`~/Projects` からの相対。使い手の仕事でない台帳（環境整備の積み残しなど）を外す
- `features`（既定 すべて false）：使い手によって使わない機能
  - `appleNotes`：Apple Notes の捕捉用のノートの取り込み（Mac のみ）
  - `done`：やったことの記録（`doc-done.mjs`）
  - `sessions`：Claude Code のセッション記録の集計（`sessions.command`）
  - `checkNames`：人名漢字の点検（`check-names.mjs`）
  - `pjLinks`：pj リンクと作業の振り分け（`pjLinks.link`・`pjLinks.open`）
  - `workload`：「やること」に見積を書き、`capacity` に収める点検（`docs/design.md`「業務量」）
- `appleNotes.title`（既定 `Capture`）：取り込むノートの名前
- `sessions.command`（`features.sessions` なら必須）：セッション記録を集計する道具を呼ぶコマンド。`ctx`（今の文脈の大きさ。10万トークンを超えたら終了コード 2）、`gaps --date <日付>`（作業ログの無いセッションの依頼の文）、`cost --since <時刻> --label <名前> --log <CSV>`（使った時間とトークン）を受けるもの
- `checkNames.sources`（既定 空）：人名漢字の照合に使う名簿のパス。`checkNames.wikiEntities`（既定 なし）：知識ベースの人物ページのフォルダ（frontmatter に `aliases` と `sources` を持つ md）
- `pjLinks.link`・`pjLinks.open`（`features.pjLinks` なら必須）：リンクを作る道具と、プロジェクトを開く道具のパス
- `attendanceNotices`（既定 空）：決まった曜日に働く別の勤務先へ、行けない・遅れる日を連絡したかを見る規則の並び（`docs/design.md`「勤務先への連絡」）。各規則の項目は次のとおりで、既定値の正本は `scripts/notices.mjs` の `RULE_DEFAULTS`
  - `label`（必須）：規則の呼び名。`attendance-notices.csv` の「規則」の列に入る
  - `weekdays`（必須）：対象の曜日（`Tue` のように `routine.json` と同じ書き方）
  - `from`・`to`（必須）：勤務先で働く時間帯。`departBy`（既定 `from`）：そこへ向けて出る時刻。`departBy` から `from` までに重なる予定は「遅れる」
  - `notifyTo`（必須）：連絡する担当者のアドレス。送信済みの照合に使う。`notifyName`（既定「勤務先の担当者」）：today.md に書く呼び名
  - `draftLine`（必須）：連絡の文面の1行。日付の後ろに付く
  - `keywords`（既定 空）：日付を書かずに当日送った連絡を拾う語
  - `skipSides`（既定 `["workplace"]`）：連絡の要らない場所。`conflict-rules.json` の `sides` による予定の場所の判定（`workplace`・`home`・`other`）で言う。`skipPattern`（既定 なし）：連絡の要らない予定の題名
  - `lookaheadDays`（既定 14）：今日から何日先まで見るか
  - 予定の場所の判定に `conflict-rules.json` を使うので、それが無いインスタンスでは警告を出して見ない
- `holidays`：祝日を読む予定表。`calendarId`（既定 Google の「日本の祝日」）、`credential`（既定 `calendars` の最初の Google の予定表のもの）。いまは `attendanceNotices` だけが使う
- `capacity`：「やること」に載せる量の上限。`dayMinutes`（既定 1080＝18時間。睡眠以外で1日に使える時間）、`days`（既定 2。何日分を載せるか）。`docs/design.md`「業務量」
- `deadlineTasks`：外部の期日を載せる Google Tasks。`credential`（既定 なし＝使わない。スコープ `https://www.googleapis.com/auth/tasks`）、`list`（既定 `締切`。無ければ作る）。タスクは持ち主にしか見えず、共有・公開した予定表に出ない
- `prepPattern`（既定 `講義|講演|レクチャー|発表|セミナー|座長|演者|lecture`）：話す側に立つかもしれない予定の題名の目印。当たった予定は候補で、話す役かは案内の本文で確かめる（`docs/design.md`「1件ずつの完了確認」）
- `credentialDir`（既定 `~/.config/gdrive-mcp`）：資格情報のフォルダ。`oauthClient`（既定 `credentials.json`）：OAuth のクライアント
- `archiveScript`（既定 `~/.claude/scripts/archive-action-items.py`）：閉じた行を退避する道具

パスは `~/` で始めてよい。

### 4.3. 例

```json
{
  "name": "planner",
  "workDoc": { "id": "<Google ドキュメントの ID>", "credential": ".google-work.json", "name": "ToDo" },
  "logDoc": { "id": "<Google ドキュメントの ID>", "credential": ".google-private.json", "name": "Log" },
  "calendars": [{ "label": "勤務先", "credential": ".google-work.json" }],
  "gmail": { "account": "taro@example.com", "credential": ".google-work.json" },
  "ledgers": { "roots": ["work"] },
  "features": { "done": true }
}
```

## 5. 動作環境

- Node.js 18 以上（`fetch` を使う）。外部のパッケージは使わない
- Mac と Windows で動くように書く。ホームは `os.homedir()` で取り（PowerShell では `HOME` が未定義のことがある）、パスは `path.join` で組む。開始時の収集は bash と jq に頼らない
- `features.appleNotes`・`features.pjLinks` は Mac だけで動く
