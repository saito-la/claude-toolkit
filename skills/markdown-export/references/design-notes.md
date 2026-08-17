# 設計メモ

過去に踏んだ落とし穴と、その原因・対処。**対処はすべて `scripts/md2docx.py`・`scripts/md2pdf.py`・`scripts/deck2pdf.py` と同梱テンプレートに内蔵済み**なので、通常の変換作業でこのファイルを読む必要はない。スクリプトやテンプレートを改修するとき、または変換結果が想定と違うときに参照する。

## md2pdf

**右余白が極端に広い** — pandoc 標準テンプレート CSS の `body{max-width:36em;padding:50px}` が原因。`body{max-width:none;padding:0}` で上書きして全幅化した。

**表の行が無駄に高い／列が均等になる** — pandoc が等幅の `<col>` を出力するため。`table-layout:auto` ＋ `col{width:auto!important}` で内容依存の列幅にした。

**タイトルが二重表示される** — pandoc `-s` の `title` はタイトルブロックを描画する。`-M pagetitle=` のみを設定し、本文 H1 と二重化させないようにした。

**ページ番号が付けにくい** — Chrome の `--print-to-pdf` はヘッダ/フッタ制御が弱い。`--no-pdf-header-footer` で既定の日付・URL を消し、PyMuPDF (fitz) で「n / N」を後段スタンプする方式にした。

**URL がクリックできない** — pandoc 標準は `<url>` 形式しかリンク化せず、単独行の裸 URL は素通しになる。`-f markdown+autolink_bare_uris` で裸 URL も `<a href>` 化した。

## deck2pdf

2026-08-17 に実測。対象は Claude Design が書き出した standalone HTML（`<section>` 10枚、画像インライン化済み）。

**グラデーション文字が途中から矩形の塗りに化ける（最重要）** — `background-clip: text` ＋ グラデーションで塗った見出しは、Chrome の PDF 出力で文字クリップとして保存されない。実測では PDF 内にテキストレンダリングモード7（クリップ）が1つも無く、代わりに画像 XObject とクリップ矩形で近似されており、「Tack så」までは字形どおり、「mycket!」は緑の矩形になった。**Playwright の `page.pdf` でも Chrome の `--print-to-pdf` でも同じように壊れる**（同じ印刷経路のため）。画面描画のスクリーンショットは正常なので、`--rasterize`（各スライドをPNGに焼いて綴じる）を用意した。

この崩れは、生成サービス側のPDF書き出しで報告されたのと同じ症状だった。つまり原因は「claude.ai を経由したこと」ではなく印刷経路そのものにある。

**1ページ目だけ見て合格にしてしまう** — 同日、対外送付するPDFを1ページ目のみ目視して添付し、最後のスライドが崩れたまま送信された。`qlmanage` は1ページ目のサムネイルしか作れず、この環境には poppler も PyMuPDF も無かったため、他ページを見る手段を用意しないまま「綺麗にできた」と判断したのが原因。**全ページの確認手段（`deck-shots.cjs` によるPNG出力）を先に用意してから送付判断をする。**

**`--size` が効かないことがある** — デッキ本体を JS で描画するエクスポートでは、注入した `@page` より後に JS が挿入するスタイルが勝つ。実測では `--size 800x450` を指定しても出力は 1440x810pt（1920x1080px 相当）のまま、デッキ自身の指定が採用された。仕上がりは崩れないため、出力時に実ページ寸法を表示して気づけるようにしてある。同じ理由で、印刷CSSに `section:not(:last-of-type){display:none}` を注入して特定ページだけ出す小技も効かない（DOM操作が要る＝Playwright 経路になる）。

**背景の帯・塗りが消える** — Chrome の `--print-to-pdf` は背景を既定で落とす。CDP 経由の `printBackground: true` に相当する指定が CLI に無いため、`print-color-adjust: exact` を注入して対処した。

**要素スクリーンショットが not visible で失敗する／スライド数が1つ多い** — デッキは画面表示では現在のスライド以外を隠すので、`emulateMedia({media:'print'})` に切り替えてから撮る必要がある。切り替えると今度は印刷用の不可視 `<section>` が1つ増えたため、実寸（100px 四方以上）を持つ要素だけをスライドとして採用している。

## md2docx

**表の罫線が透明** — テンプレートの表スタイルの枠線が透明だった。`tblBorders`（濃い灰色 0.75pt）を明示付与した。

**「(1)(2)…」の箇条書きが空の中黒＋入れ子番号に割れる** — 半角丸括弧数字を pandoc がファンシー順序リストと誤認するため。**回避策は md 側で全角「（1）」を使うこと**（SKILL.md 本文に規則として記載）。

**地の文だけの md（メール文面等）で段落間の余白が一切つかない** — 2026-07-25 に判明。pandoc は本文の各段落に `BodyText`、先頭段落に `FirstParagraph` というスタイルIDを割り当てるが、テンプレート（`reference-gothic.docx`／`reference-default.docx`）側にこの2スタイルの定義が無く、`Normal` スタイルの余白設定も継承されなかった。docDefaults にも既定の段落間隔が無いため、Word・QuickLook などどのビューアで開いても段落同士がベタ詰めになる。

対処：テンプレートの `styles.xml` に `BodyText`・`FirstParagraph` を明示追加した（`Normal` を basedOn にしつつ `w:spacing w:after="200"`＝10pt を直接指定し、継承任せにしない）。**修正したのは `templates/*.docx` の中身だけで、スクリプト側は変更していない。**

この不具合は見出しや表と違って一見して段落境界が分かりにくく、生成物を開かないと気づけない。そのため SKILL.md 本文に「プレーンな地の文中心の文書は変換後に必ず実際の見た目を確認する」という規則を置いている。
