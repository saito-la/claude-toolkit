# schedule-reply — 日程調整メール自動回答

シングルユーザー向け・要個別セットアップ。Gmail の inbox を定期ポーリングし、**tonton / 調整さん**
の日程調整入力依頼を検知して、Google カレンダーの空きを `config.availability` で設定した
営業時間で判定し、回答欄を `config.displayName` の名義で自動入力する。処理済みメールは `Scheduling` ラベルへ
移動して既読化する。

他の同梱スキルと異なり、本ツールは **Google Calendar/Gmailへの書き込み・自動送信を伴う** ため、
自動セットアップの対象外（`install.py`はSKILL.md自体のコピーのみ行う）。導入する場合は本READMEに沿って手動で
`config.json` を作成し、自分の氏名・除外条件・認証情報を設定すること。

## フロー

```
in:inbox 検索 ─▶ 分類/除外 ─▶ 回答ページを開く ─▶ 候補取得 ─▶ 空き判定(◯/△/✕)
   │                                                            │
   └── config.exclude の送信者/件名パターンに一致 → スキップ       ▼
                                          config.displayName の名義で入力・送信
                                                    │
              変更可能URLをブラウザで開く ◀── 送信完了 ──▶ Scheduling へ移動＋既読
```

## 対応ツールと自動化レベル

| ツール | 候補取得 | 空き判定 | 入力・送信 |
|---|---|---|---|
| **調整さん** (chouseisan.com) | 埋め込みJSON `choices` から取得 | ◯/△/✕（枠単位） | **全自動**：名前入力→各候補を alt 属性で ◯/△/✕ クリック→フォーム送信 |
| **tonton** (tonton.amaneku.com) | グリッド見出しの日付から取得 | 日単位◯/△/✕（ログ表示）＋30分枠単位 | **全自動**：`/add.php` へ回答フォームを直接POST（`mtgtime_flg_N` 文字列） |

tonton のタイムラインはセルがクリック不能（0サイズspan・JS不透明）のため、UI操作ではなく
**回答を直接POST**する。各日 `mtgtime_flg_N`（48文字、位置=00:00からの30分刻み）で、空き＝`5`(○)・
予定あり／時間外＝`1`(×)・非提案＝`-`。**全提案枠を○か×で明示**（空白を残さない）。ログの日単位◯/△/✕は
概況で、実体は30分枠単位。tonton は後から編集するため `browser.tontonEditPassword` の設定必須
（同名＋パスワードで既存回答を削除して上書きするため。未設定だと再回答が重複登録になる）。

## コードと個人データの分離

スクリプト本体はリポジトリ、個人データはリポジトリ外に置く。

- **コード（正本）** — このディレクトリ（`skills/schedule-reply/scripts/`）。git 管理。
- **個人データ** — `~/.config/schedule-reply/`（`XDG_CONFIG_HOME` があればそちら）。`config.json`・`state.jsonl`（処理済み記録）・`logs/` の3点。

`poll.mjs` は `~/.config/schedule-reply/config.json` があればそれを既定の config とし、**`state.jsonl` と `logs/` も config と同じディレクトリに置く**。無ければスクリプト同梱の `config.json` にフォールバックするので、単体で自己完結させたい使い方も従来どおり動く。

分けておく理由＝`git pull`・再 clone・skill の再配置（symlink ⇄ copy）で、個人設定と「どのメールに回答済みか」の記録が巻き添えにならないため。処理済み記録が消えると同じ日程調整に二重回答する。

## セットアップ

```bash
SCRIPTS=~/.claude/skills/schedule-reply/scripts   # skill の配置先

# 1) 依存（Playwright）。ネットワーク不可時はグローバル版をリンク（下記フォールバック）
cd "$SCRIPTS" && npm install

# 2) 設定ファイル（個人データ側に作る）
mkdir -p ~/.config/schedule-reply
cp "$SCRIPTS/config.example.json" ~/.config/schedule-reply/config.json
chmod 700 ~/.config/schedule-reply && chmod 600 ~/.config/schedule-reply/config.json
#   - displayName / 除外リスト / businessStart-End を自分の値に設定
#   - gmail/calendar の credentialsPath を自分のGoogle OAuth認証情報のパスに合わせる
#   - flags.dryRun を false にすると送信あり（既定運用）。true なら判定のみ

# 3) 実行（cwd 非依存。config は自動で ~/.config/schedule-reply/config.json が使われる）
node "$SCRIPTS/poll.mjs"            # 送信あり（config.flags.dryRun=false のとき）＋送信後にブラウザで確認
node "$SCRIPTS/poll.mjs" --send     # config に関わらず強制的に送信
node "$SCRIPTS/poll.mjs" --dry-run  # 送信せず判定のみ（安全確認・冪等）
node "$SCRIPTS/poll.mjs" --send --only <Gmailメッセージid>  # inbox内の該当1通だけを処理（他の未処理案件を巻き込まない）
```

`--verbose` を付けると、実際に読んだ config のパスが1行目に出る。config を複数使い分けるときは `--config <path>` で指定する（`state.jsonl`・`logs/` もそのパスの隣に作られる）。

都度手動で回す場合は上記コマンドを実行するだけ（launchd 不要）。送信すると回答ページを既定ブラウザで開き、内容確認を促す。

**会話経由で「このメールに回答して」と特定の1通を指定されたときは、必ず `--only <Gmailメッセージid>` を付ける。** 省略すると inbox 内の他の未処理案件（別件の tonton/調整さん）も一括で自動送信されてしまう。

Claude Codeスキルとしての導入方法は `../SKILL.md` および claude-toolkit トップの README を参照。

### Playwright フォールバック

`npm install` がネットワークで失敗する場合、グローバル導入済みの Playwright をローカルにリンクする
（ブラウザ本体は共有キャッシュを利用）：

```bash
mkdir -p node_modules
ln -sf /opt/homebrew/lib/node_modules/playwright node_modules/playwright
node -e "import('playwright').then(m=>console.log('OK', !!m.chromium))"
```

## 本番投入の順序

1. **`--dry-run` で判定表を確認**（誤検知・除外もれがないか）。
2. **送信を有効化**：`config.json` の `flags.dryRun` を `false` に（調整さん・tonton とも送信経路は検証済み）。
   - tonton は後から編集できるよう `browser.tontonEditPassword` を設定推奨。
3. **ラベル移動・既読化を有効化**：Gmail に `gmail.modify` スコープを追加（下記）。
4. **定期実行を登録**（launchd、任意）。

### Gmail スコープ拡張

relabel / 既読化に必須。使用中のGmailトークンが `gmail.readonly + gmail.compose` のみの場合、
**ラベル移動・既読化はできない**。Gmail認証スクリプトの `SCOPES` に
`https://www.googleapis.com/auth/gmail.modify` を追加して再認証する。

未付与のままでも判定・入力・送信は動作し、relabel だけ自動スキップ（警告表示）。

### launchd 定期実行

15分間隔でポーリングする（任意・省略可）。`schedule-reply.plist.example` の `YOUR-USERNAME` を自分のものに置き換えてから使う。

```bash
cp schedule-reply.plist.example ~/Library/LaunchAgents/com.schedule-reply.plist
# ~/Library/LaunchAgents/com.schedule-reply.plist 内の YOUR-USERNAME を置換してから：
launchctl load  ~/Library/LaunchAgents/com.schedule-reply.plist   # 登録・起動
launchctl list | grep schedule-reply                                # 稼働確認
tail -f ~/.config/schedule-reply/logs/launchd.log                   # 実行ログ

# 停止・解除
launchctl unload ~/Library/LaunchAgents/com.schedule-reply.plist
```

`config.flags.dryRun=true` の間は launchd 実行でも判定のみで送信しない（安全既定）。

## 制約・ルール

実装済みの制約は以下。

- 対象は **inbox 内のみ**（`in:inbox`）。処理後は `Scheduling` へ移動し inbox から外す＝再検知防止。
- 空き判定は `config.availability` で設定した営業日・時間帯（既定：平日8:00–17:00）。範囲外は候補にしない。
- 参照カレンダーは `config.calendar.calendarIds` で指定したもののみ。
- **除外**：`config.exclude.senders`（送信者）・`config.exclude.subjectPatterns`（件名パターン）に一致するメール。ログにスキップ理由を残す。
- 検知は**メインメッセージ（引用・過去スレッドを除いた本文冒頭）**で判定。対象URLと入力依頼の両方がメイン部にある場合のみ動作。議事録・通知・返信引用にだけ URL があるメールは自動スキップ（メインが日程調整依頼でないため）。
- tonton は**ログインしない**（公開ページ扱い）。
- 個人データ（`config.json`・`state.jsonl`・`logs/`）は `~/.config/schedule-reply/` に置きリポジトリに入れない（上記「コードと個人データの分離」）。同名ファイルをこのディレクトリに置いた場合に備えて `.gitignore` にも入れてある。

## 判定ロジック

- **時刻つき候補**（調整さん「10:00〜11:00」等）：時間外/週末→✕、空き→◯、一部重複→△、全重複→✕。
- **日単位候補**（tonton の日付列）：会議長（既定60分）以上の空き枠が業務時間内に…
  - 無い/週末 → ✕、有り かつ 業務時間の半分以上空き → ◯、有るが混雑 → △。
- タイムゾーンは Asia/Tokyo 固定。
- 空き判定は **freeBusy（config.calendar.calendarIds）** に基づく。予定あり・不在・仮予定（未確定）など不透過イベントは全て busy として反映され、空き時間のみ ◯。判定はポーリング検知時点のカレンダーで行うため、回答後にカレンダーを変更した場合は内容がずれ得る（tonton は同名＋パスワードで再送信すれば既存回答を削除して上書きする）。

## ファイル構成

コード（git 管理）:

```
schedule-reply/
├── SKILL.md                     # Claude Codeスキル（呼び出し口）
└── scripts/
    ├── poll.mjs                    # オーケストレータ（エントリ）
    ├── run.sh                      # launchd 起動ラッパ（PATH整備のみ）
    ├── schedule-reply.plist.example # launchd定義のひな型（要パス置換）
    ├── config.example.json         # 設定ひな型
    └── lib/
        ├── google.mjs              # OAuth更新＋Gmail/Calendar REST（fetchのみ・依存ゼロ）
        ├── classify.mjs            # ツール判別・入力依頼判定・除外
        ├── availability.mjs        # 平日8-17＋freeBusy → ◯/△/✕
        ├── browser.mjs             # Playwright起動＋アダプタ振り分け＋確認用オープン
        ├── adapters/tonton.mjs     # tonton（/add.php 直接POST・mtgtime_flg・重複時は削除して上書き）
        ├── adapters/chousei.mjs    # 調整さん（埋め込みJSON＋表UI）
        ├── logger.mjs              # 要約出力＋JSONLログ
        └── state.mjs               # 処理済みメールの重複防止
```

個人データ（リポジトリ外・バックアップ対象）:

```
~/.config/schedule-reply/
├── config.json      # 自分の設定（chmod 600 推奨）
├── state.jsonl      # 処理済みメール記録（消すと二重回答の恐れ）
└── logs/scheduler.log
```

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `gmail.modify スコープ未付与` 警告 | 上記「Gmail スコープ拡張」で再認証 |
| `Cannot find package 'playwright'` | 上記フォールバックで symlink、または `npm install` |
| 候補が取れずスキップ | ポーリング締切/リンク切れ。手動対応（state に記録され再試行しない） |
| 調整さんの送信不可 | 先方UI変更。`alt`属性（`<候補>_まる/さんかく/ばつ`）の書式を確認 |
| tonton の入力が反映されない／位置ズレ | `/add.php` の `mtgtime_flg_N` 書式変更を確認（位置=HH×2+MM/30、`5`○/`1`×/`-`非提案） |
| tonton が重複登録される | `browser.tontonEditPassword` 未設定。設定すれば再回答時に旧エントリを削除して上書き |
