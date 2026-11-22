# Orca Bots で秘書の bot を作る

作成日：2026-11-22
改訂日：2026-11-22

## 1. 目的

Orca の bot 機能（Orca Bots）で、全プロジェクトのセッションを立ち上げ、指揮し、進み具合を知らせる秘書の bot を1つ作り、Mac と iPhone・iPad から話しかけられるようにする。bot は専用の AI を持たず、置いた Mac に入っている Claude Code（または Codex）で動き、費用はそのサブスクに乗る。仕組みは公式の [Bots](https://orca.dev/docs/bots)・[Security](https://orca.dev/docs/bots/security)・[Mobile](https://orca.dev/docs/mobile) にある。Orca そのものの入れ方と画面での設定は [README.md](README.md)。

## 2. 前提

- Orca 1.4.265 以降（2026-11-19 に bot 機能が入った）。Claude Code が入っていて、ログイン済みであること
- bot は置いた Mac が起きている間だけ動く。普段使う Mac に置くなら、蓋を閉じても眠らない設定にしておく（`sudo pmset -a disablesleep 1`。鞄の中で熱を持つことがあるので、使い方に合わせて決める）。常時動く Mac があれば、そちらに置いてもよい
- iPhone・iPad から使うなら、App Store の Orca Mobile と、Orca のアカウント（Relay を使うため）

## 3. bot を作る

1. Orca のサイドバーの + から New bot を選ぶ
2. 名前と、役割の1行を書く。例：「〇〇の秘書・執事・相棒。全プロジェクトのセッションを立ち上げ、指揮し、進み具合を知らせる。実作業は各セッションに任せる」
3. 置く機（Local Mac）とエージェント（Claude Code）を選び、Create bot を押す。bot は人がこのボタンを押したときだけ作られる

bot のフォルダは `~/.orca/bots/<名前の小文字>/` にできる。中身は次のとおり。

- `SOUL.md`：役割。持ち主が書くファイル（5節）
- `MEMORY.md` と `memory/`：記憶。1行に1つの事実
- `AGENTS.md`・`CLAUDE.md`・`bot.json`：Orca が書く。手で直しても戻される

## 4. 設定

bot を選び、設定の画面で次のようにする。

- Permissions：Approve for me。既定の Full access は、確認なしで何でも実行させる。Approve for me は Claude Code の auto mode に当たる（設定の値が `auto` になる）
- Who can ask：Only you。他人が頼めるようにすると、その人が持ち主の Mac を持ち主の権限で使えることになる
- Memory upkeep：知識を別の正本（手順書・wiki など）に置いているなら止める。記憶が正本と2か所になり、ずれるため。記憶を使う場合、`MEMORY.md` の行を消すには `orca bot memory forget --fact "<その行の文>"` を使う。ほかの方法で消すと戻される
- Look around：初めは既定のまま（平日の日中に4時間おきの見回り）。見回りは読むだけで、書き込み・実行・送信はできない。何を知らせてくるかを見て、うるさければ止める
- Notifications：完了・許可待ち・失敗の3つを受ける
- Connectors：初めは足さない。Slack・GitHub・Linear・Webhook からの依頼は Orca のクラウドを経由する

## 5. SOUL.md

`SOUL.md` は bot の役割を書くファイル。作った直後は、役割の1行と、Orca の既定の禁止（持ち主が許すまで、ここ以外への投稿や変更をしない）だけが入っている。

持ち主に依らない秘書の役割（受け持つこと・しないこと・返事の形）は、このフォルダの [bot-SOUL.md](bot-SOUL.md) に書いてある。bot の `SOUL.md` をこれへの symlink に置き換えて使う。Orca が作った `SOUL.md` は、念のため名前を変えて残す。

```bash
BOT=~/.orca/bots/<名前の小文字>
TOOLKIT=<claude-toolkit の clone>   # 配布パッケージなら ~/Projects/saito-la/claude/vendor/claude-toolkit
mv "$BOT/SOUL.md" "$BOT/SOUL.orig.md"
ln -s "$TOOLKIT/tools/orca/bot-SOUL.md" "$BOT/SOUL.md"
```

持ち主が誰か、何を大事にしているかは書かず、グローバルの指示（`~/.claude/CLAUDE.md`）に任せる。bot は Claude Code として動くのでそれを読む。効いているかは8節で確かめる。持ち主に固有の受け持ち（別の機に開く、など）が要るなら、`bot-SOUL.md` を写して直すのではなく、グローバルの指示の側に書く。

## 6. iPhone・iPad をつなぐ

### 6.1. つなぎ方の選び方

Settings → Mobile の Connection は2つある。

- Orca Relay：携帯の回線でも、どの Wi-Fi でも届く。Orca のアカウントへのサインインが要る
- LAN：同じ Wi-Fi か Tailscale でしか届かない。アカウントは要らない

移動中に使うなら Orca Relay にする。LAN でペアリングすると、スマホはそのときの Mac のアドレス1つにつなぐので、Mac のアドレスが変わると（テザリング、出先の Wi-Fi）届かなくなる。

Relay の作りは次のとおり（2026-11-22、Orca のソースで確かめた）。

- Relay で作るペアリングのコードには、Mac への直結の宛先と Relay の両方が入る。スマホは直結を先に試し、届かなければ Relay に回る
- 中身は、直結でも Relay でも、スマホと Mac の間で暗号化され、Orca のクラウドは中身を読めない。確かめたのは作りの名前と型までで、暗号の実装の監査はしていない
- Mac は Orca のクラウドへ外向きにつなぎに行くので、Mac の口を外に開けておく必要は無い

### 6.2. 手順

1. Mac の Orca の Settings → Orca Account でサインインする
2. Settings → Mobile の Paired devices に、LAN で作った古いペアリングがあれば、ゴミ箱のボタンで消す。新しく作るものも「Mobile <日付>」の名前になり見分けられないので、先に消す。スマホの Orca Mobile からも古い Mac を消す
3. Connection で Orca Relay を選ぶ。This computer's address は既定のままでよい（Tailscale のアドレスが出ていれば、スマホの Tailscale が生きているときだけ直結に使われる）
4. Generate QR code を押し、iPhone の Orca Mobile の Pair Desktop で読む。iPad も同じようにする
5. iPhone の Wi-Fi を切り、携帯の回線だけで Mac のセッションと bot が見えることを確かめる

### 6.3. Mac の口

Orca はスマホ用の口（6768番）を、Mac がつないでいるすべての網で待ち受ける。ペアリングの鍵と暗号化で守られてはいるが、Relay でつなぐなら LAN からの直結は要らない。病院や出先の網につなぐ Mac では、macOS の pf で Tailscale（100.64.0.0/10 と fd7a:115c:a1e0::/48）と自機だけに絞るとよい。設定には管理者のパスワードが要る。絞る仕組み（LaunchDaemon）は、まだこの配布物に入っていない。

## 7. 声で使う

- 話して頼む：Orca Mobile の入力欄のマイクで口述する（iOS 26 以降は端末の中で文字にする）。Mac では Settings → Voice
- 返事を聞く：Orca に読み上げは無い。iPhone・iPad の Speak Screen（Settings → Accessibility → Spoken Content → Speak Screen をオンにし、画面の上から2本指で下へなぞる）で読ませる
- 声だけで対話する機能は、2026-11 の時点で Orca に無い

## 8. 作ったあとに確かめること

- bot のチャットで、グローバルの指示が効いているか（言葉づかい、決まりごと）
- 1通の返事にかかる時間。セッション開始時のフックが重いと遅れる
- 許可の確認が出る操作と出ない操作（Approve for me が auto mode に当たるか）
- bot から `orca` のコマンドと、手元の道具が呼べるか
- iPhone・iPad から、携帯の回線で bot と話せるか
