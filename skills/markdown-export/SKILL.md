---
name: markdown-export
description: markdownファイルをWord(.docx)やPDFに変換したいとき（「Wordにして」「PDFにして」「docxで出力して」「印刷用に整形して」等）に使うスキル。pandocを直接使わず、必ずこのスキルのスクリプトを経由する。
---

# Markdown → Word / PDF 変換

このスキルのディレクトリに同梱された `scripts/md2docx.py`・`scripts/md2pdf.py` を使って、markdown を体裁の整った Word / PDF に変換する。pandoc を直接呼び出さない（表の列幅・日本語レイアウト・ページ番号などの後処理を自動化しているため）。

**このSKILL.mdが置かれているディレクトリ（このファイルの絶対パス）を基準に、`scripts/md2docx.py`・`scripts/md2pdf.py` を実行すること。**symlink経由で読み込まれていても、実体のディレクトリを解決してから使う。

## Word変換

```bash
python3 <このSKILL.mdのディレクトリ>/scripts/md2docx.py input.md                      # gothicテンプレート（既定）
python3 <このSKILL.mdのディレクトリ>/scripts/md2docx.py input.md --template default
python3 <このSKILL.mdのディレクトリ>/scripts/md2docx.py input.md -o output.docx
python3 <このSKILL.mdのディレクトリ>/scripts/md2docx.py input.md --page-numbers --fit-title
```

pandoc変換＋表の列幅自動調整＋表罫線付与を一括で行う。テンプレートは同梱の `templates/reference-gothic.docx`（全文ゴシック）・`templates/reference-default.docx`（游明朝/游ゴシック標準）。

## PDF変換

```bash
python3 <このSKILL.mdのディレクトリ>/scripts/md2pdf.py input.md                       # A4・余白2cm・ページ番号付き
python3 <このSKILL.mdのディレクトリ>/scripts/md2pdf.py input.md -o out.pdf
python3 <このSKILL.mdのディレクトリ>/scripts/md2pdf.py input.md --margin 15mm --no-page-numbers
```

pandoc → 整形HTML → Chrome印刷 → ページ番号スタンプ、の3段パイプライン。A4・上下左右2cm均等・本文全幅・内容依存の表列幅・クリック可能なURL・下中央のページ番号「n / N」が既定の仕上がり。

## 複数md結合PDF

複数のmdファイルをそれぞれPDF化したうえで、表紙・目次・PDFしおり（ブックマーク）・通しページ番号付きの1冊に束ねたい場合（調査ダイジェスト集・添付資料集の分冊など）は `scripts/combine-pdfs.py` を使う。pandoc直呼び禁止と同じ理由で、複数PDFのマージ手順もその場限りのワンオフでなく本スクリプト経由に統一する。

```bash
python3 <このSKILL.mdのディレクトリ>/scripts/combine-pdfs.py \
    -o out.pdf --title "海外制度調査 参考資料集" --subtitle "米国編" \
    --author "東京大学医学部附属病院 臨床研究推進センター" --date "2026-07-07" \
    --style digest.style.html \
    us-nci-cancer-centers.md us-nci-nctn.md us-ctsa.md ...
```

各mdの1行目 `# 見出し` をタイトルとして自動取得し、表紙に目次（開始頁付き）、本文にPDFしおり（既定で「資料1　タイトル」形式）を生成する。本文側のH1は書き換えない（しおりのみに資料番号を付与）。`--style` を渡すと全パート・表紙に同一CSSを強制適用できる（`--no-cover` で表紙・目次生成を省略し単純結合のみも可能）。

## 依存関係

```bash
brew install pandoc
pip3 install python-docx lxml pymupdf
```

PDF生成には Google Chrome（等の Chromium系ブラウザ）も必要。

## md 執筆時の規則

変換先の都合で、md 側で守らないと崩れるものがある。

- **箇条書きの番号ラベルは全角「（1）」を使う。** 半角「(1)」は pandoc がファンシー順序リストと誤認し、Word で空の中黒＋入れ子番号に割れる。
- **プレーンな地の文中心の文書（メール文面等）を docx 化したら、変換後に実際の見た目を確認する。** 見出しや表と違って段落境界が一見して分からず、余白の不具合に気づけない。`qlmanage -t -s 1600 -o <出力先> <file>.docx` で PNG 化すれば Read ツールで目視できる。

## 文書固有のスタイル追加

入力と同名の兄弟ファイル `<入力>.style.html`（例: `foo.md` → `foo.style.html`）を置くと、既定CSSの**後**に追加 include され、後勝ちで上書きできる（md2pdf）。無ければ既定CSSのみ。

## 設計メモ

過去に踏んだ落とし穴とその原因分析は `references/design-notes.md`。**対処はすべてスクリプトと同梱テンプレートに内蔵済み**なので、通常の変換では読む必要がない。スクリプト・テンプレートを改修するとき、または変換結果が想定と違うときに参照する。
