# box-api

作成日：2026-11-24

## 1. 目的

### 1.1. 何をするか

Box API を、アカウントごとのトークンで直接呼ぶ小さな呼び出し口。Box CLI の代わりに、自動処理と Claude Code から Box を使うときの入口にする。Python の標準ライブラリだけで動き、macOS 標準の Python 3.9 でも動く。

### 1.2. Box CLI をやめる理由

Box CLI 4.x（macOS）は、全アカウントの設定を Keychain の1項目にまとめ、「既定のアカウント」を切り替えて使う作りになっている。

- 起動時にその項目を読めないと、全アカウントの設定を空で上書きする
- 書き込みは削除してから追加する2手で、その間は項目が存在しない

そのため、並行に動くと全アカウントの設定を失う（2026-11-24 に実際に起きた）。Keychain は ssh のセッションから読めないので、ssh 越しにも使えない。

box-api は共有の「既定」を持たず、アカウントごとに別のファイルへトークンを置く。同時に動いても互いを壊さず、ssh からも動く。

## 2. 設計

- 設定とトークンは `~/.config/box-api/<アカウント>.json`（権限 600、ディレクトリは 700）に置く。中身は client_id・client_secret・refresh_token・access_token・期限。書き込みは一時ファイルからの rename で、途中で落ちても壊れない
- アクセストークンは期限の5分前に自動で更新する。Box の refresh_token は使い捨てで、使うと新しいものに替わり、古い方は無効になる。更新はアカウントごとの flock の下で1プロセスずつ行い、待っていた側は更新済みのトークンを使う
- 401 は1回だけ更新してやり直す。429・5xx は Retry-After に従って待ち、やり直す
- refresh_token は60日使わないと失効する。そのときは `login` で入り直す
- 同じアカウントを複数の Mac で使うときは、Mac ごとに `login` する。ファイルを写して共用すると、片方の更新でもう片方の refresh_token が無効になる

## 3. 準備

### 3.1. アプリ

テナントごとに、開発者コンソール（`https://<テナント>.app.box.com/developers/console`）で Platform App を1つ作る。

1. 認証方法は「User Authentication (OAuth 2.0)」にする
2. Redirect URI に `http://localhost:3000/callback` を登録する（Box CLI と同じ。`login --port` で変えたらそれに合わせる）
3. 使う操作に合わせてスコープを選ぶ。ファイルの読み書きなら Read/Write all files and folders、ユーザー・グループ・監査ログには管理用のスコープが要る
4. テナントの Enterprise 設定「デフォルトで未公開Platformアプリを無効にする」がオンなら、テナントの管理者に有効にしてもらう

### 3.2. ログイン

```bash
box-api login <アカウント> --client-id <Client ID>   # Client Secret はプロンプトに貼る（表示されない）
box-api accounts                                     # 設定済みのアカウントとログインの状態
box-api <アカウント> whoami
```

## 4. 使い方

```bash
box-api <アカウント> get folders/0/items --query limit=1000 --all     # 一覧を最後まで
box-api <アカウント> get files/123 --query fields=name,size
box-api <アカウント> put folders/123 --data '{"name":"新しい名前"}'
box-api <アカウント> post collaborations --data @collab.json
box-api <アカウント> delete collaborations/456
box-api <アカウント> download 123 ./dl/                               # ディレクトリなら元の名前で保存
box-api <アカウント> upload 789 ./資料.pdf                            # フォルダ 789 へ新規
box-api <アカウント> upload-version 123 ./資料.pdf                    # 既存ファイル 123 の新しい版
```

パスは `https://api.box.com/2.0/` からの相対で書く。応答は JSON のまま標準出力に出るので、`python3 -c` や `jq` で読む。50MB を超えるアップロード（分割アップロード）には未対応。

## 5. 検証

```bash
python3 tools/box-api/test_box_api.py
```

本物の Box には触れず、手元に立てた偽の Box サーバーで確かめる。確かめる項目は次のとおり。

- ログイン
- 設定ファイルの権限
- 期限切れでの自動更新
- 8プロセスが同時に更新しても更新は1回で全員が通ること
- 401 と 429 のやり直し
- 一覧の全ページ読み（offset・marker）
- ダウンロード（リダイレクト）
- 日本語のファイル名のアップロード
- 失効したときと設定が無いときに止まること
