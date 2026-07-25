# 設計メモ

過去に踏んだ落とし穴と、その原因・対処。**対処はすべて `scripts/md2docx.py`・`scripts/md2pdf.py` と同梱テンプレートに内蔵済み**なので、通常の変換作業でこのファイルを読む必要はない。スクリプトやテンプレートを改修するとき、または変換結果が想定と違うときに参照する。

## md2pdf

**右余白が極端に広い** — pandoc 標準テンプレート CSS の `body{max-width:36em;padding:50px}` が原因。`body{max-width:none;padding:0}` で上書きして全幅化した。

**表の行が無駄に高い／列が均等になる** — pandoc が等幅の `<col>` を出力するため。`table-layout:auto` ＋ `col{width:auto!important}` で内容依存の列幅にした。

**タイトルが二重表示される** — pandoc `-s` の `title` はタイトルブロックを描画する。`-M pagetitle=` のみを設定し、本文 H1 と二重化させないようにした。

**ページ番号が付けにくい** — Chrome の `--print-to-pdf` はヘッダ/フッタ制御が弱い。`--no-pdf-header-footer` で既定の日付・URL を消し、PyMuPDF (fitz) で「n / N」を後段スタンプする方式にした。

**URL がクリックできない** — pandoc 標準は `<url>` 形式しかリンク化せず、単独行の裸 URL は素通しになる。`-f markdown+autolink_bare_uris` で裸 URL も `<a href>` 化した。

## md2docx

**表の罫線が透明** — テンプレートの表スタイルの枠線が透明だった。`tblBorders`（濃い灰色 0.75pt）を明示付与した。

**「(1)(2)…」の箇条書きが空の中黒＋入れ子番号に割れる** — 半角丸括弧数字を pandoc がファンシー順序リストと誤認するため。**回避策は md 側で全角「（1）」を使うこと**（SKILL.md 本文に規則として記載）。

**地の文だけの md（メール文面等）で段落間の余白が一切つかない** — 2026-07-25 に判明。pandoc は本文の各段落に `BodyText`、先頭段落に `FirstParagraph` というスタイルIDを割り当てるが、テンプレート（`reference-gothic.docx`／`reference-default.docx`）側にこの2スタイルの定義が無く、`Normal` スタイルの余白設定も継承されなかった。docDefaults にも既定の段落間隔が無いため、Word・QuickLook などどのビューアで開いても段落同士がベタ詰めになる。

対処：テンプレートの `styles.xml` に `BodyText`・`FirstParagraph` を明示追加した（`Normal` を basedOn にしつつ `w:spacing w:after="200"`＝10pt を直接指定し、継承任せにしない）。**修正したのは `templates/*.docx` の中身だけで、スクリプト側は変更していない。**

この不具合は見出しや表と違って一見して段落境界が分かりにくく、生成物を開かないと気づけない。そのため SKILL.md 本文に「プレーンな地の文中心の文書は変換後に必ず実際の見た目を確認する」という規則を置いている。
