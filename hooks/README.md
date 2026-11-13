# hooks

作成日：2026-09-28
改訂日：2026-11-13

Claude Code のフックとして `~/.claude/hooks/` に置くスクリプト。Google Chat への完了通知、Markdown 見出しの規約検査、相手に届く送信の検査、削除の検査、応答の言語の検査と、これらの検査を Codex CLI でも効かせる仲介を収める。

## 配置と登録

### 配置

`install.py` が、このフォルダの `*.sh`・`*.py` を `~/.claude/hooks/` へ置く。README と `webhook.env.example` は置かない。POSIX では symlink、Windows ではコピーになるのは他の配布物と同じで、上流で消えたものは次の実行で撤去される。

### 登録

置いただけではフックは動かない。`~/.claude/settings.json` の `hooks` から呼んで初めて効く。

- 完了通知：`install.py` が `Stop` と `UserPromptSubmit` に登録する。登録するのは `~/.config/claude-chat-notify/webhook.env` がある端末だけで、`--no-settings` を付けたときと、既に `notify-chat` を呼ぶ登録があるときは足さない。Webhook を使わない人の設定にフックを増やさないための条件なので、`webhook.env` を後から置いたら `install.py` をもう一度実行する
- 見出しの検査：`install.py` は登録しない。規約を機械で守らせたい範囲に応じて、ユーザーの `settings.json` か、プロジェクトの `.claude/settings.json` に自分で足す（下の「Markdown 見出しの検査」）
- 送信の検査：`install.py` は登録しない。ユーザーの `settings.json` に自分で足す（下の「相手に届く送信の検査」）
- 削除の検査：`install.py` は登録しない。ユーザーの `settings.json` に自分で足す（下の「削除の検査」）
- 応答の言語の検査：`install.py` は登録しない。ユーザーの `settings.json` に自分で足す（下の「応答の言語の検査」）
- Codex CLI での検査：`install.py` は登録しない。`/etc/codex/hooks.json` に管理者権限で置く（下の「Codex CLI での検査」）

## Google Chat への完了通知

### 概要

依頼した処理が全部片づいたことを Google Chat のスペースへ知らせる。離席していても終わりに気づくためのもの。

既定では鳴らない。頼まれたときだけ Claude が有効にする（下の「有効にする」）。

送る本文は、端末名・プロジェクト名・時刻と、有効にしたときに書いた1行の要約だけ。それ以上は載せない。スペースは他の人と共有されうるうえ、作業内容には人事・個人情報・未公開の検討が混ざりうるため、本文を組み立てる余地をスクリプトの外へ広げない。

```
✅ Claude 完了 — my-laptop / my-project 10:05
SDTM の適合性検証を回す
```

3本の役割は次のとおり。

- `notify-chat.sh`：`Stop` から呼ぶ本体。有効になっていれば予約を立て、静かな時間が続いたら送る
- `notify-chat-start.sh`：`UserPromptSubmit` から呼ぶ。新しい依頼が入ったら予約を取り消す
- `notify-chat-arm.sh`：頼まれたときに Claude が実行し、有効にする

### 有効にする

頼まれたときに Claude が `notify-chat-arm.sh` を実行して有効にする。頼まれ方・コマンド・`once`／`session`／`--off`・要約に書いてよいことは `instructions/tools.md`「作業完了の Chat 通知」が正本。

### 通知が出る条件

有効になっていることに加えて、次を満たしたときに送る。

- 応答が終わってから 20 秒のあいだ、次の依頼が入らないこと。応答のたびに送るのではなく予約を立て、静かな時間が続いたときだけ裏で待っている自分自身が送る。キューに依頼を積んでおくと消化のたびに予約が取り直されるので、鳴るのは全部片づいてからの1回だけになる。待ち時間は `webhook.env` の `CHAT_NOTIFY_QUIET_SECONDS` で変えられる
- サブエージェントの完了でないこと。`SubagentStop` では鳴らさない。知りたいのは全部終わったことで、途中の区切りではないため。フックの入力からイベント名が取れない版では、`Stop` として扱う

通知が紐づくのは Claude の応答完了であって、裏で走っているジョブの完了ではない。10 分を超える処理を `run_in_background` で投げた場合、ジョブより先に応答が返って通知が出ることがある。その形で使うなら、ジョブ側の完了を検知する仕組みを別に足す。

### 既に開いているセッションへの反映

`settings.json` のフック定義は、既に開いているセッションにも効く（2026-08-30 実測。セッション開始後にマージしたフックがそのまま発火した）。

一方、`~/.claude/instructions/` はセッション開始時に読まれるので、開いたままのセッションには反映されない。通知の頼み方を新しく配っても、そのセッションの Claude は手順を知らないままになる。更新したら区切りのよいところで開き直す。

有効化のフラグは全セッション共通の1ファイルなので、どのセッションから有効にしても他のセッションの完了で拾う。開き直せないときは、別のセッションから、または `!` を付けて直接 `notify-chat-arm.sh` を実行すればよい。

### Webhook URL の置き場

`~/.config/claude-chat-notify/webhook.env`（権限 600）。リポジトリには置かない（`conventions/secret-handling.md`）。このフォルダの `webhook.env.example` をコピーして URL を入れる。

このファイルが無い端末では、3本とも何もせずに終わる。URL を配っていない端末で作業が止まることはない。

### スペース側の準備

Webhook は DM には作れない。通知を受けたい人とのスペースを1つ作り、そのスペースのメニューから「アプリと統合」→「Webhook を追加」→ 名前（「Claude」等）を付けて URL を発行する。メニューに Webhook の項目が出なければ、組織の Workspace 管理者が無効化している。

### 導入と確認

```bash
mkdir -p ~/.config/claude-chat-notify
cp <claude-toolkit>/hooks/webhook.env.example ~/.config/claude-chat-notify/webhook.env
chmod 600 ~/.config/claude-chat-notify/webhook.env
# webhook.env を開いて CHAT_WEBHOOK_URL に実際の URL を入れる（値を画面に出さない）
python3 <claude-toolkit>/install.py      # webhook.env を置いたので、ここで Stop・UserPromptSubmit に登録される
~/.claude/hooks/notify-chat.sh --test
```

`--test` は経過時間の判定を飛ばして即座に送る。スペースに「✅ Claude 通知テスト — 端末名 / プロジェクト名 時刻」が出れば配線できている。URL は `&` を含みうるので、`sed` の置換で書き込まない（置換側の `&` が一致した文字列に化ける）。

## Markdown 見出しの検査

### 検査する内容

`md-heading-convention.py` は `PostToolUse`（`Write|Edit`）で呼ぶ。書いたファイルが `.md` なら見出し行を走査し、`conventions/living-doc-structure.md` の規約に反するものを見つけると、該当する見出しを標準エラーに出して終了コード 2 で返す。Claude はその指摘を受けて自分で直す。

- 見出しの括弧書き（副題・確認日・「（案）」等）
- ヘッジ語（たたき台・ドラフト・draft・暫定）
- 0 始まりの章・節・ステップ番号（`## 0.`・`Step 0`・`第0章` 等）

コードブロックの中は見ない。作業ログ・変更履歴・llm-wiki のように過去の見出しをそのまま残す文書は対象外にしている。

### 登録

全プロジェクトに効かせるならユーザーの `~/.claude/settings.json`、特定のリポジトリだけならそのリポジトリの `.claude/settings.json` の `hooks` に足す。

```json
"PostToolUse": [
  {
    "matcher": "Write|Edit",
    "hooks": [
      {
        "type": "command",
        "command": "python3 \"$HOME/.claude/hooks/md-heading-convention.py\"",
        "statusMessage": "見出し規約チェック"
      }
    ]
  }
]
```

スクリプトが置かれていない端末と共有するプロジェクトの設定に書くなら、存在を確かめてから呼ぶ。`python3` は開けないファイルを渡されると終了コード 2 で終わるため、確かめないと全ての書き込みが止まる。

```json
"command": "f=\"$HOME/.claude/hooks/md-heading-convention.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\""
```

## 相手に届く送信の検査

### 検査する内容

`outbound-send-guard.py` は `PreToolUse`（`Bash`）で呼ぶ。コマンド文字列と、コマンドが実行するスクリプトファイル（`.py`・`.mjs`・`.js`・`.cjs`・`.ts`・`.sh`・`.rb`）の中身に次が含まれていれば、`permissionDecision` を `ask` にして返す。自動許可の設定や auto mode の下でも確認の画面が出て、送るかどうかをユーザーが決める。

- Google Calendar の招待・変更の通知（`sendUpdates` が `all`・`externalOnly`、旧来の `sendNotifications=true`）
- Gmail の送信（`/messages/send`・`/drafts/send`、クライアントライブラリの `.send(`）
- Slack への投稿（`chat.postMessage`）
- MCP のツールのうち呼んだ時点で相手に届くもの（Gmail の `schedule_draft`）。ツール名の末尾で照合する

送信を含まない Calendar・Gmail の操作（`sendUpdates=none`、下書きの作成・更新・削除）は通す。原則は `instructions/principles.md` の「相手に届く操作は、送ることを明示されたときだけ行う」。

### 経緯

2026-10-05、「カレンダー招待を作る」という提案への承認を送信の承認と取り違え、Calendar API を `sendUpdates=all` で呼んで社外の演者と関係者へ招待を送った。Calendar の MCP は通知を付けずに作るので、送信が起きるのは Calendar・Gmail の API を Bash から直接呼ぶ経路で、そこには確認の関門が無かった。指示だけでは同じ取り違えを防げないため、送信の直前で機械的に止める。

### 限界

検査は文字列の照合なので、送信の指定を変数で組み立てたり、別ファイルから読み込んだりすると見逃す。スクリプトを書く段階（ヒアドキュメントでファイルに書くコマンド）でも、文字列が含まれていれば確認が出る。

### 登録

`install.py` は登録しない。全プロジェクトに効かせるため、ユーザーの `~/.claude/settings.json` に足す。`PreToolUse` で `python3` が終了コード 2 を返すとすべての Bash が止まるので、スクリプトの存在を確かめてから呼ぶ。

```json
"PreToolUse": [
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "f=\"$HOME/.claude/hooks/outbound-send-guard.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\"",
        "statusMessage": "送信の検査"
      }
    ]
  }
]
```

MCP の送信を検査するには、同じコマンドを `"matcher": "mcp__.*__schedule_draft"` の組でも登録する。

## 削除の検査

### 仕組み

ファイルを消す経路を `scripts/dedup-rm.py` の1本にし、`delete-guard.py` がそれ以外の経路を止める。部品はこの2つだけで、隔離・保管・台帳は持たない。

- `dedup-rm.py`：渡したパスを、隠しファイルと gitignore されたものまで含めてファイル1つずつに展開し、ファイルごとに同じ中身がほかにあるかを消す直前に確かめる。比べる先は、上流へ push 済みの git の履歴、`--against` で指定した手元・別の端末のファイル、Google Drive・Box などが返すハッシュ。写しと生成物だけを消し、唯一の中身は残して一覧を出す。唯一の中身を消すのは、`--unique-ok` の TSV に1件ずつ「何か・消す理由・消してよい根拠」を書き、`--dry-run` で出した一覧をユーザーが見て承認したときだけ。使い方はスクリプトの冒頭
- `delete-guard.py`：`PreToolUse`（`Bash`）で呼ぶ。守る場所（既定は `~/Projects`、環境変数 `CLAUDE_PROTECTED_ROOTS` で変える）の中を `rm`・`unlink`・`find -delete`・`git clean -f`・外への `mv`・`rsync --delete` で消すコマンドと、`unison -force` を `deny` で止め、dedup-rm を使うよう返す。`ssh` の先と `bash -c` の中も見る。生成物（`.DS_Store`・`~$*`・`__pycache__`・`node_modules`）と `.git` の中の `*.lock` は通す

### 経緯

削除の事故は、どれも Claude がシェルで直接消し、消す前に確かめた範囲と実際に消した範囲が食い違って起きた。

- 同期ツールの強制解決（`unison -force`）を「片側から反対側へ写す」と取り違え、片側にしか無いファイルを消した
- pull を止めていた追跡外のファイルを、git の追跡外のファイルの一覧（`--exclude-standard`）で「上流と同じ」と確かめたうえで、フォルダごと退避して pull し、退避先を消した。一覧は gitignore されたファイルを数えないので、同じフォルダの唯一のファイルが確かめた範囲から漏れていた。報告は「何も失っていない」だった
- 前日に「追跡外で上流と同じ」と確かめたファイルを、その後に別の作業で追跡された後になって消した

消した後に保管しておく案（隔離）は採らなかった。消すときと同じ判定を後でかけても結果は変わらず、判定の誤りは直らないため。誤りの原因は、判定した対象と消した対象のずれと、判定と削除の時点のずれで、どちらも、消すファイルそのものを消すその場で判定すれば起きない。

「整理して」は、正本に同じ中身がある写しの削除を含む。唯一の中身の削除は整理ではなくデータを捨てることなので、理由と根拠を示して承認を取る。

### 限界

検査はコマンドの文字列を読むので、Python の `os.remove`・`shutil.rmtree` のようにスクリプトの中で消すもの、`git reset --hard`・`git checkout --` のように未コミットの変更を捨てるものは止めない。

### 登録

全プロジェクトに効かせるため、ユーザーの `~/.claude/settings.json` の `PreToolUse`（`Bash`）に足す。

```json
{
  "type": "command",
  "command": "f=\"$HOME/.claude/hooks/delete-guard.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\"",
  "statusMessage": "削除の検査"
}
```

## 応答の言語の検査

### 仕組み

`language-guard.py` は `instructions/language.md`（応答は日本語）を機械で守らせる。2つのイベントで呼ぶ。

- `SessionStart`（matcher `compact`）：`language.md` を標準出力に出し、文脈の要約の直後に規則を入れ直す
- `Stop`：そのターンの最終応答で、和文字が和文字と英字の合計の 25% 未満なら `decision: "block"` を返し、日本語で出し直させる。コードブロック・インラインコード・引用（`>` の行）・URL・パスは数えず、40字未満の応答は判定しない。出し直しの後（`stop_hook_active`）は止めない。そのターンの依頼に「英語で」「in English」等があれば検査しない

### 経緯

2026-11-05、ある1つのセッションで、文脈の要約の後に最終応答が3回英語になった。`language.md` は CLAUDE.md から読み込まれているが、要約は英語で書かれ、要約後の文脈の大半が英語の資料（メール・FHIR の JSON）になると、規則が埋もれて英語に引きずられる。規則の文を強めても同じ条件で再発するため、要約の直後に規則を入れ直す段と、出力の後に機械で止める段を足した。直近 40 セッションの 292 ターンで判定を試し、引っかかった 5 件はすべて実際に英語の最終応答だった。

### 限界

ツール呼び出しの間の文は見ない（出し直させても画面からは消えない）。思考（visible thinking）も見ない。英語の固有名詞と識別子が大半を占める短い日本語の応答は、英語と判定されることがある。

### 登録

```json
"SessionStart": [
  {
    "matcher": "compact",
    "hooks": [
      { "type": "command", "command": "f=\"$HOME/.claude/hooks/language-guard.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\"" }
    ]
  }
],
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "f=\"$HOME/.claude/hooks/language-guard.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\"",
        "statusMessage": "応答の言語の検査"
      }
    ]
  }
]
```

## Codex CLI での検査

### 仕組み

`codex-guard.py` は Codex CLI の `PreToolUse` で呼び、Claude Code の検査（`~/.claude/hooks/*-guard.py`）をそのまま走らせる。どの検査をどのツールに掛けるかは `~/.claude/settings.json` の `PreToolUse` を読み、`matcher` が当てはまり、コマンドが `~/.claude/hooks/` の `*-guard.py` を呼ぶ登録だけを実行する。Codex は Claude と同じ形のツール名（シェルは `Bash`、MCP は `mcp__<server>__<tool>`）と `tool_input` を渡すので、検査の側には手を入れない。検査を足し替えたときに Codex 側を直す必要は無い。

Codex の `PreToolUse` は `permissionDecision: "ask"` を扱えず、フックの失敗として記録したうえでツールを実行してしまう（[Codex の hooks の文書](https://learn.chatgpt.com/docs/hooks)）。そのため `ask` を `deny` に読み替える。Codex は確認の画面を出せないので、送る・消すと決めたら人が自分で実行する。

### 経緯

2026-11-13、Codex に Claude Code と同じ MCP を登録し、サンドボックスを外した。Claude Code で効いている削除と送信の検査が Codex では1つも動かないため、Codex だけが歯止めの無い状態になった。

### 限界

検査の限界はそれぞれの検査と同じ。Codex のフックは文書の上でも「完全な強制境界ではなくガードレール」で、`WebSearch` のようなホスト側のツールは通らない。

### 登録

`~/.codex/hooks.json` には置かない。ユーザーの層のフックは、Codex の `/hooks` で人が1件ずつ信頼するまで動かず（`config.toml` の `hooks.state` に記録する内容のハッシュが要る）、`codex exec` では黙って飛ばされる。システムの層（`/etc/codex/`）のフックは管理されたものとして扱われ、信頼の手続き無しに動き、無効にもできない。Orca などが `~/.codex/hooks.json` に書き込むものと混ざらない利点もある。

```bash
sudo install -d -m 755 /etc/codex
sudo install -m 644 ~/Projects/claude-toolkit/hooks/codex-system-hooks.json /etc/codex/hooks.json
```

Claude Code の `!` から打つと端末が無く、`sudo` はパスワードを読めずに止まる。その場合は macOS のパスワードの画面を出す形で置く（Claude に実行させてもよい）。

```bash
osascript -e 'do shell script "install -d -m 755 /etc/codex && install -m 644 '"$HOME"'/Projects/claude-toolkit/hooks/codex-system-hooks.json /etc/codex/hooks.json" with administrator privileges'
```

置いたあと、`codex exec` に守る場所のファイルの `rm` と MCP の `schedule_draft` を試させ、両方が止まることを確かめる（2026-11-13、okra の Codex 0.160.0 で確認）。

`codex-guard.py` 自体は `install.py` が `~/.claude/hooks/` へ置く。置かれていない端末ではフックは何もしない。

## 未コミットのリポジトリの検査

`uncommitted-repos-check.py` は SESSION-END.md の Step 5（commit & push）を機械で支える。Stop で呼ぶ。

- 検査の対象は cwd と、セッション中に Write・Edit したファイルが属する全リポジトリ。cwd だけを見ると、別プロジェクトへ書いた成果物の未コミットを見逃す（2026-11-11、jaspehr のセッションが `presentation` へ書いたデッキが未追跡のまま残った）
- 直近のユーザー発言に「終了・終わり・終え」があり、未コミット・未 push があれば block して commit & push させる。それ以外は警告（systemMessage）だけで、同じセッションでは30分に1回まで
- fetch はしない。未 push は upstream（無ければ `origin/main`）との差で数える

```json
"Stop": [
  { "hooks": [ { "type": "command", "command": "f=\"$HOME/.claude/hooks/uncommitted-repos-check.py\"; [ -f \"$f\" ] || exit 0; python3 \"$f\"" } ] }
]
```
