# title-url-bookmarklet

現在開いているページの「タイトル + 改行 + URL」を1クリックでクリップボードにコピーするブックマークレット。Claude Codeに「このページの内容を教えて」等と伝える際、参照URLをタイトル付きで貼り付けられる。

## インストール

**ドラッグ&ドロップ**：[`../../docs/index.html`](../../docs/index.html)（GitHub Pages公開後は公開URL）を開き、「Title and URL」リンクをブックマークバーへドラッグ&ドロップする。

**手動貼り付け**：

1. ブックマークバーで右クリック→「ページを追加」等で新規ブックマークを作成
2. 名前は任意（例「Title and URL」）
3. URL欄に以下を貼り付けて保存

```
javascript:var global=window;global.COPY_TO_CLIPBOARD=global.COPY_TO_CLIPBOARD||{};global.COPY_TO_CLIPBOARD.getUrlInfo=function(){var a=new String(document.title);a.allReplace=function(a){var b=this,c;for(c in a)b=b.replace(new RegExp(c,"g"),a[c]);return b}.bind(a);return a.allReplace({":":"\uff1a","\\[":"\uff3b","\\]":"\uff3d"})+"\n"+document.URL}; global.COPY_TO_CLIPBOARD.copyToClipboard=function(){var a=document.createElement("textarea");a.textContent=this.getUrlInfo();var d=document.getElementsByTagName("body")[0];d.appendChild(a);a.select();var b=document.execCommand("copy");d.removeChild(a);return b};global.COPY_TO_CLIPBOARD.copyToClipboard();
```

読みやすい元ソース（コメント付き）は [`title-url-bookmarklet.js`](title-url-bookmarklet.js)。

## 使い方

任意のページでこのブックマークをクリックするだけ。「タイトル\nURL」の形でクリップボードに入る。タイトル中の `:` `[` `]` は全角（`：` `［` `］`）に自動置換される（Markdown等に貼り付けたときの記法事故を防ぐため）。

## 仕組み

1. `document.title` を取得し、`:` `[` `]` を全角文字に置換
2. 置換後のタイトル + 改行 + `document.URL` を組み立てる
3. 非表示の `<textarea>` を body に追加して選択し、`document.execCommand("copy")` でコピー（`navigator.clipboard.writeText` ではなく `execCommand` を使うため、クリップボード権限プロンプトが出ない）

## 制約

- `document.execCommand("copy")` は非推奨APIだが、権限プロンプト無しで動く実利があるため使用している。将来のブラウザで廃止された場合は `navigator.clipboard.writeText` への切り替えが必要
