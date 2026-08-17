---
name: markdown-export
description: markdownファイルをWord(.docx)やPDFに変換したいとき（「Wordにして」「PDFにして」「docxで出力して」「印刷用に整形して」等）、およびスライドHTML（Claude Design 等が書き出す standalone HTML のデッキ）をPDFにしたいときに使うスキル。pandocを直接使わず、必ずこのスキルのスクリプトを経由する。
---

# Markdown → Word / PDF 変換

同梱スクリプトを使って、markdown を体裁の整った Word / PDF に変換する。**pandoc を直接呼び出さない**（表の列幅・日本語レイアウト・ページ番号などの後処理を自動化しているため）。スライドHTMLのPDF化（deck2pdf）も本スキルが持つ。

以下 `$SKILL` は**このSKILL.mdが置かれているディレクトリの絶対パス**。symlink 経由で読み込まれていても実体のディレクトリを解決して使う。オプションは各スクリプトの `--help` が正本。

```bash
python3 $SKILL/scripts/md2docx.py input.md                  # → docx（既定は全文ゴシックテンプレート）
python3 $SKILL/scripts/md2pdf.py input.md                   # → PDF（A4・余白2cm・ページ番号付き）
python3 $SKILL/scripts/combine-pdfs.py -o out.pdf --title "..." a.md b.md ...   # 複数mdを1冊に結合
python3 $SKILL/scripts/deck2pdf.py deck.html                 # → PDF（1スライド=1ページ）
python3 $SKILL/scripts/deck2pdf.py deck.html --rasterize     # → PDF（画面描画を焼く・崩れない）
```

- **md2docx**：pandoc変換＋表の列幅自動調整＋表罫線付与。テンプレートは同梱の `templates/reference-meiryo.docx`（全文メイリオ・本文10pt・行間0.85・既定）、`templates/reference-gothic.docx`（全文ゴシック・本文10.5pt）、`templates/reference-default.docx`（游明朝/游ゴシック標準）。meiryo は名前付きスタイル自体にフォント・サイズ・行間を持たせてあり、Google Docs へ変換したあとも既定の書式が保たれる（`markdown-to-gdocs` の `formal-ja` プリセットと同値）。
- **md2pdf**：pandoc → 整形HTML → Chrome印刷 → ページ番号スタンプの3段。本文全幅・内容依存の表列幅・クリック可能なURL・下中央の「n / N」が既定の仕上がり。
- **combine-pdfs**：各mdの1行目 `# 見出し` をタイトルとして自動取得し、表紙に目次（開始頁付き）、本文にPDFしおり（既定「資料1　タイトル」形式）を生成する。本文側のH1は書き換えない。調査ダイジェスト集・添付資料の分冊など、複数PDFをまとめる場面ではワンオフのマージをせず本スクリプトに統一する。
- **deck2pdf**：スライドHTML（standalone）を同じ Chromium に印刷させて PDF にする。生成サービス側のPDF書き出しや pptx エクスポートは別のレイアウトエンジンで組み直すため崩れる（グラデーション文字が落ちる、テキスト幅が変わって不要な改行が入る）。**外部依存のあるHTMLは不可**——画像やフォントが外部参照だと欠ける。

## スライドPDFの作り分け

deck2pdf には2経路あり、既定はベクター（テキストが選択でき、軽い）。

- **既定（ベクター）**：`--print-to-pdf` でそのまま印刷する。多くのデッキはこれで足りる。
- **`--rasterize`**：各スライドを画面描画のPNGに焼いてから1枚1ページで綴じる（要 playwright）。**`background-clip: text` によるグラデーション文字を含むデッキは、ベクター経路だと途中から矩形の塗りに化けるため、こちらを使う。** テキスト選択はできず、容量は2倍前後になる。

**対外送付するPDFは全ページを目視してから渡す。** 1ページ目だけ確認して送ると、装飾の崩れは最後のスライド（謝辞・締めのページに凝った装飾が来やすい）で見落とす。ページ抽出ができない環境では `--rasterize` の中間PNGか、`deck-shots.cjs` を直接叩いて各スライドのPNGを出して確認する。

```bash
NODE_PATH=$(npm root -g) node $SKILL/scripts/deck-shots.cjs deck.html <出力先> 8000 section 2
```

## md 執筆時の規則

変換先の都合で、md 側で守らないと崩れるものがある。

- **箇条書きの番号ラベルは全角「（1）」を使う。** 半角「(1)」は pandoc がファンシー順序リストと誤認し、Word で空の中黒＋入れ子番号に割れる。
- **プレーンな地の文中心の文書（メール文面等）を docx 化したら、変換後に実際の見た目を確認する。** 見出しや表と違って段落境界が一見して分からず、余白の不具合に気づけない。`qlmanage -t -s 1600 -o <出力先> <file>.docx` で PNG 化すれば Read ツールで目視できる。

## 文書固有のスタイル追加

入力と同名の兄弟ファイル `<入力>.style.html`（例: `foo.md` → `foo.style.html`）を置くと、既定CSSの**後**に追加 include され、後勝ちで上書きできる（md2pdf・combine-pdfs の `--style`）。無ければ既定CSSのみ。

## セットアップ・設計メモ

- 依存関係（pandoc・python-docx・Chrome 等）は `references/setup.md`。
- 過去に踏んだ落とし穴とその原因分析は `references/design-notes.md`。**対処はすべてスクリプトと同梱テンプレートに内蔵済み**なので、通常の変換では読む必要がない。スクリプト改修時、または変換結果が想定と違うときに参照する。
