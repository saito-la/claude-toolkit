# Apple Notes への送信手順

「Notesに送って」「メモに送って」等、まとめ・行程・チェックリストを **Apple Notes（iPhoneで閲覧前提）** に送る指示を受けたときの再現手順。**`~/.claude/NOTES-SEND-local.md` があれば併せて読む**（無ければスキップ）。個人固有の定型ルーチン（特定の通知メールを決まったノートへ転記する等）はそちらにある。

## ワークフロー

1. **まず PlainText でプレビューを表示し、いったん止める。** ユーザーの「OK」を得てから送信する。
2. OK後、下記の方法で Notes.app にノートを作成する。
3. 作成後、**読み戻して内容と反映サイズを確認**し、結果を報告する。

## 作成方法

HTML本文を一時ファイルに書き出し、osascript で読み込んで作成する（インラインの巨大文字列はエスケープが破綻するため必ずファイル経由）。

```bash
osascript <<'EOF'
set theFile to "/absolute/path/to/note.html"
set theBody to read (POSIX file theFile) as «class utf8»
tell application "Notes"
	set newNote to make new note with properties {body:theBody}
	return "CREATED|" & (name of newNote) & "|" & (id of newNote)
end tell
EOF
```

- 本文の **1行目がノートのタイトル**になる。
- 内容は **プレーンテキスト相当**（Markdown記号は使わない）。改行は `<div>` または `<br>`。

## md からの変換

md ファイルを送るときは `scripts/md-to-notes.py` を使う。既定はプレビューの出力だけで、`--send` で作成と読み戻しの確認まで行う。H2 の節ごとに分ける `--split-h2`、作成先のフォルダ `--folder` がある。Notes 側で直接直したときは、`--diff` で md との差分を出し、md に取り込んでから `--send` で送り直す（同名のノートは本文が置き換わるので、取り込む前に送ると Notes の編集が消える）。使い方はスクリプト冒頭の docstring が正本。

## フォントサイズ

2026-10-02 の実測では、Notes は取り込み時に `font-size` を縮小せず、指定の px をそのまま保った（28px・34px の指定が 28px・34px のまま残った）。そのため `scripts/md-to-notes.py` は承認済みのサイズ（本文19px・見出し23px）を直接指定している。下の表は、縮小が起きていた時期（〜2026-08）の実測で、手で HTML を書くときは読み戻した `font-size` を見てどちらの挙動かを確かめる。

Notesは取り込み時にインライン `font-size` を**縮小して正規化**する。実測の対応：

| HTML指定 | Notes反映（iPhoneで読みやすい承認済みサイズ） |
|---|---|
| `font-size:28px` | 本文 **19px** |
| `font-size:34px` | 見出し・タイトル **23px** |

→ **本文は `font-size:28px`、見出し・タイトルは `font-size:34px`＋`<b>`** で指定すれば、ユーザーが「OK」とした読みやすいサイズ（本文19px／見出し23px）が再現される。デフォルトのまま送ると小さすぎる（過去の不満点）ので必ずこの指定を付ける。

## HTMLテンプレート

```html
<div style="font-size:28px; line-height:1.55;">
<div style="font-size:34px;"><b>【タイトル＝1行目】</b></div>
<br>
<b>■セクション見出し</b><br>
本文行1<br>
本文行2<br>
<br>
<b>■次のセクション</b><br>
...
</div>
```

## 確認

```bash
osascript <<'EOF'
tell application "Notes"
	return body of note id "x-coredata://.../ICNote/pXXX"
end tell
EOF
```

- `<span style="font-size: 19px">` 等が付いていれば正しく反映されている。
- 縮小され過ぎ・付いていない場合は上記の指定サイズを見直す。

## 補足

- 経路案内など「何号車・ドアの左右」を求められた場合、号車と前後ドア位置は一次情報（駅探・乗換ガイド等）で確定できるが、開くドアの左右は信頼できる情報が取れないことが多い。取れない項目は捏造せず「車内表示で確認」と明記する。
