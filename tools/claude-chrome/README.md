# claude-chrome

作成日：2026-11-26
改訂日：2026-11-26

## 1. 目的

### 1.1. 何をするか

AI（Claude Code）がブラウザを操作するための Chrome を、人が使う Chrome と分けて起動・操作する macOS の呼び出し口。`install.py` が `~/.local/bin/claude-chrome` に置く。

- プロファイルは `~/.config/claude-chrome/<フォルダ>` に置く。端末ごとに番号が変わる Chrome の「Profile 3」に頼らず、どの Mac でも同じパスになる
- 専用の user-data-dir なので、remote debugging（CDP）が使える。既定の user-data-dir では Chrome 136 以降これが拒否される（`DevTools remote debugging requires a non-default data directory`）。Playwright は `chromium.connectOverCDP('http://127.0.0.1:<ポート>')` で後から接続し、プロファイルをロックしないので、人が開いたタブと自動の処理が同居できる
- Dock と Cmd+Tab のアイコンに「AI」の印とプロファイル名の帯を付け、人が使う Chrome と、プロファイルどうしを見分けられるようにする（4節）

### 1.2. 使う場面

Claude がブラウザでログインの要るサービスを読む・操作するとき、人に一度ログインしてもらい、その後の操作を Claude が続けるとき。ログインはプロファイルに残るので、次からは人の手が要らない。

## 2. 使い方

`--<呼び名>` は `profiles.conf` の1列目（3節）。省くと1行目のプロファイルを使う。

```bash
claude-chrome start                      # 起動（起動済みなら空タブの補充とアイコンの掛け直しだけ）
claude-chrome --private open <URL>       # URL を開いて前面に出す。人がログインするときの入口
claude-chrome tab <URL>                  # 前面へ出さずにタブを開き、targetId を出す（スクリプト用）
claude-chrome tabs                       # 開いているタブの URL
claude-chrome release [--match <正規表現>] [targetId...]   # 後始末（6節）
claude-chrome status                     # CDP に応答するか
claude-chrome stop                       # 終了
claude-chrome profiles                   # 設定されたプロファイルと起動の有無
```

Playwright からは、`start` の後に `connectOverCDP` で接続する。新しいタブは `ctx.newPage()` ではなく、CDP の `Target.createTarget` に `background: true` を付けて開く（5節）。

## 3. プロファイルの設定

`~/.config/claude-chrome/profiles.conf` に、1行に1つの Chrome を書く。列は空白で区切り、`#` で始まる行は読まない。書き方の見本は [profiles.conf.example](profiles.conf.example)。

```
# 呼び名   フォルダ          CDPポート  帯の文字  帯の色
work      profile           9222      Work      #D97757
private   profile-private   9223      Private   #1A73E8
```

- 呼び名：`claude-chrome --<呼び名>` で選ぶ名前
- フォルダ：`~/.config/claude-chrome/` の下の user-data-dir。1つのフォルダには1つの Google アカウント（組織）だけをログインさせる
- CDP ポート：プロファイルごとに変える
- 帯の文字・帯の色：アイコンの下の帯（4節）。文字に空白は使えない

ファイルが無ければ、`~/.config/claude-chrome/profile`・ポート 9222 だけを使い、帯はフォルダ名を灰色で出す。

どのアカウントをどの呼び名にするかは使い手ごとに違うので、このリポジトリには置かない。配布する側の非公開のリポジトリに置き、そこから `~/.config/claude-chrome/profiles.conf` へリンクを張る。

環境変数 `CLAUDE_CHROME_PROFILE`（フォルダの絶対パス）・`CLAUDE_CHROME_PORT`・`CLAUDE_CHROME_LABEL`・`CLAUDE_CHROME_COLOR` は、`--<呼び名>` を付けないときに設定より優先する。`CLAUDE_CHROME_PROFILE` だけを渡したときは、そのフォルダの行のポートと帯を使う。`CLAUDE_CHROME_CONF` で設定ファイルの場所を変えられる。

## 4. アイコン

AI 用の Chrome は人が使う Chrome と同じ `Google Chrome.app` から起動するので、何もしないと Dock と Cmd+Tab に同じアイコンが並ぶ。`start` は起動のたびに、CDP の `Browser.setDockTile` でそのプロセスのアイコンを差し替える。

- 左上の「AI」の丸が AI 用の印で、人が使う Chrome には付かない
- 下の帯がプロファイルで、`profiles.conf` の帯の文字と色で描く
- 画像は [icon/make-icon.js](icon/make-icon.js) が Chrome の `app.icns` から作り、`~/.config/claude-chrome/icons/` に置く。macOS に最初から入っている JXA（`osascript -l JavaScript`）で描くので、Xcode の Command Line Tools が無い端末でも作れる。差し替えは [icon/set-dock-tile.mjs](icon/set-dock-tile.mjs)
- 差し替えはプロセスが終わるまで有効。Chrome を起動し直したら `start` で掛け直す（起動済みでも `start` を呼べば掛かる）。Cmd+Tab の名前は「Google Chrome」のままで、変えられるのはアイコンだけ
- 帯の文字か色を変えると、新しい画像を作る。同じ文字と色のまま描き方だけを変えたときは、`icons/` の古い画像を消す

`Google Chrome.app` を `~/Applications/` へ複製して別のアプリにする案は採らない。複製は署名が有効なまま動くが、起動した複製が Google Updater に自分を Chrome の本体として登録し直し、`/Applications/Google Chrome.app` が更新の対象から外れる（2026-10-16 実測。`~/Library/Application Support/Google/GoogleUpdater/prefs.json` の `ecp` と `pv_path` が複製を指した）。Info.plist を書き換えて名前も変える案は、署名が合わなくなり、キーチェーンの「Chrome Safe Storage」へのアクセスの確認が Chrome の更新のたびに出る。

## 5. 前面を取らない

AI がブラウザを操作している間も、人は同じ Mac で別の作業をしている。Chrome が前面に出ると、打っていた文字とフォーカスが Chrome へ移る。

- 起動した Chrome は、`open -g` で起動しても自分で前面へ出る（最初の約5秒に何度か取る）。`start` は、起動の前に前面だったアプリを覚え、その間に Chrome が前面を取ったら戻す。人が自分で別のアプリへ移ったときは触らない
- `/json/new` と Playwright の `ctx.newPage()` は、タブを開いた瞬間に Chrome を前面へ出す。`Target.createTarget` に `background: true` を付けて開いたタブは前面を動かさない。`tab` と、`start` が補う空タブはこの形で開く
- 人に見せたいとき（ログインを頼むとき）だけ `open` か `page.bringToFront()` で前面に出す

## 6. 後始末

`connectOverCDP` の `browser.close()` は切断だけで、タブも Chrome も残る。無人のスクリプトは終わりに必ず `release` を通す（`finally` や `trap`）。自分が開いたタブ（targetId か、URL が `--match` に当たるもの）を閉じ、中身のあるタブが1枚も残らなければ Chrome も終了する。人が開いたタブが残っていれば終了しない。Node から呼ぶときは [release.mjs](release.mjs) の `releaseChrome` を import する。規則と限界は同ファイルの冒頭。

macOS では全ウィンドウを閉じても Chrome のプロセスが残り、CDP も応答し続ける。その状態は browser context が無いため、`connectOverCDP` が `Browser context management is not supported` で失敗する。`start` は空タブを1枚立てて、この状態を解消する。

## 7. 前提と制約

- macOS 専用（`open`・`osascript`・`sips`・Dock を使う）。`install.py` は Windows には置かない
- Google Chrome が `/Applications/Google Chrome.app` にあること
- Node.js 22 以降（組み込みの `WebSocket` と `fetch` を使う）と python3
- ssh のセッションからも起動できる。実体を直接起動せず `open` で起動するので、GUI のセッションの Chrome になり、保存済みのログインを読める
- `--load-extension` は効かない。拡張は、起動した窓の `chrome://extensions` から手で入れる
- ログイン（Cookie）は Mac ごとに別で、同期しない。Mac ごとに1回ログインする
