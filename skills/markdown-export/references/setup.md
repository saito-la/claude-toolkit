# markdown-export セットアップ

```bash
brew install pandoc
pip3 install python-docx lxml pymupdf
```

PDF生成には Google Chrome（等の Chromium 系ブラウザ）も必要。

deck2pdf の `--rasterize`（スライドを画面描画のPNGに焼く経路）だけは playwright を使う。グローバル導入でよく、`NODE_PATH` はスクリプトが `npm root -g` から自動解決する。

```bash
npm i -g playwright && npx playwright install chromium
```
