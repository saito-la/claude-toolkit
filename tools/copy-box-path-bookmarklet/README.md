# copy-box-path-bookmarklet

Box.com のファイル/フォルダページで、Box内のフォルダパス（`/Box/親フォルダ/.../ファイル名`）+ URL を1クリックでクリップボードにコピーするブックマークレット。Box以外のページでは [title-url-bookmarklet](../title-url-bookmarklet/README.md) と同じ「タイトル+URL」コピーに自動フォールバックする。

## インストール

**ドラッグ&ドロップ**：[`../../docs/index.html`](../../docs/index.html)（GitHub Pages公開後は公開URL）を開き、「Copy Box path」リンクをブックマークバーへドラッグ&ドロップする。

**手動貼り付け**：

1. ブックマークバーで右クリック→「ページを追加」等で新規ブックマークを作成
2. 名前は任意（例「Copy Box path」）
3. URL欄に以下を貼り付けて保存

```
javascript:var global = window;global.COPY_TO_CLIPBOARD = global.COPY_TO_CLIPBOARD || {};global.COPY_TO_CLIPBOARD.confirmUrlExistence = function(url){ return fetch(url) .then(response => { if (!response.ok) { throw new Error(response.statusText); } return response.json(); }).catch(error => { console.log("Fetch failed.", error) });};global.COPY_TO_CLIPBOARD.createTargetFolderPath = function(targetFolderId){ return Box.prefetchedData["/app-api/enduserapp/current-user"].preview.appHost + "app-api/enduserapp/folder/" + targetFolderId;};global.COPY_TO_CLIPBOARD.createTargetFilePath = function(targetFileId){ return Box.prefetchedData["/app-api/enduserapp/current-user"].preview.appHost + "app-api/enduserapp/item/f_" + targetFileId + "?format=preview";};global.COPY_TO_CLIPBOARD.getBoxFolderPath = function(json){ return json.folder.path.map(i => i.name).filter((_, idx) => idx > 0);};global.COPY_TO_CLIPBOARD.getUrlInfo=function(){ let a = new String(document.title); a.allReplace = function(a){ let b=this,c; for(c in a)b=b.replace(new RegExp(c,"g"),a[c]);return b }.bind(a); return a.allReplace({":":"\uff1a","\\[":"\uff3b","\\]":"\uff3d"})+"\n"+document.URL;};global.COPY_TO_CLIPBOARD.copyTextAndTitle=function(){ navigator.clipboard.writeText(this.getUrlInfo());};global.COPY_TO_CLIPBOARD.copyToClipboard = async function(){ if (!(/^.*\.box\.com.*$/.test(document.URL))){ console.log("Does not work outside Box web."); this.copyTextAndTitle(); return; }; const fileId = document.URL.split("/").pop().split("?")[0]; if (!(/^\d+$/.test(fileId))){ return; }; const isFolder = /folder/.test(document.URL); let fileName = null; let folderId = fileId; if (!isFolder){ const jsonFile = await this.confirmUrlExistence(this.createTargetFilePath(fileId)); if (!jsonFile){ console.log("Failed to obtain file information."); return; }; fileName = jsonFile.items[0].name; folderId = jsonFile.items[0].parentFolderID; }; const jsonFolder = await this.confirmUrlExistence(this.createTargetFolderPath(folderId)); if (!jsonFolder){ console.log("Failed to obtain folder information."); return; }; const arrayFolder = this.getBoxFolderPath(jsonFolder); const joinFolderName = arrayFolder.join("/"); const folderAndFileName = fileName !== null ? joinFolderName + "/" + fileName : joinFolderName; const boxPathStr = "/Box/" + folderAndFileName + "\n"; const res = boxPathStr + document.URL; navigator.clipboard.writeText(res); console.log(res); return;};global.COPY_TO_CLIPBOARD.copyToClipboard();
```

読みやすい元ソース（コメント付き）は [`copy-box-path-bookmarklet.js`](copy-box-path-bookmarklet.js)。

## 使い方

Box.com のファイル/フォルダ詳細ページ、または任意のページでこのブックマークをクリックするだけ。Box上なら「`/Box/親フォルダ/.../ファイル名`\nURL」、Box以外なら「タイトル\nURL」がクリップボードに入る。

## 仕組み

1. `document.URL` が `*.box.com` でなければ、タイトル+URLコピーにフォールバックして終了
2. URL末尾（クエリを除く）をIDとして抽出。数字でなければ何もしない
3. URLに `folder` を含むかでファイル/フォルダを判定
4. ファイルの場合：Box内部API（`app-api/enduserapp/item/f_<id>?format=preview`）からファイル名と親フォルダIDを取得
5. Box内部API（`app-api/enduserapp/folder/<id>`）でフォルダの祖先パスを取得し、ルート直下（マイファイル等）を除いた名前配列を作る
6. `/Box/親フォルダ/.../ファイル名` + 改行 + `document.URL` を `navigator.clipboard.writeText` でコピーする

## 制約・既知の脆弱性

- グローバル `Box.prefetchedData["/app-api/enduserapp/current-user"]`（Box Web UIがページ読み込み時に埋め込む内部データ）に依存する。Box側のUI・内部API変更で壊れる可能性がある
- Box内部APIエンドポイント（`app-api/enduserapp/folder/*`・`app-api/enduserapp/item/*`）は非公開APIであり、Box側の変更で予告なく壊れる可能性がある。壊れた場合はDevTools Consoleでエラーを確認しつつ本ファイルを更新する
- URL末尾のIDが数字でない形式（Boxの共有リンク等）には対応していない
