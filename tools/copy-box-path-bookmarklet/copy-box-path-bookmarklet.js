/**
 * Box.com のファイル/フォルダページで、Box内のフォルダパス（/Box/親フォルダ/.../ファイル名）+ URL を
 * クリップボードにコピーするブックマークレット。Box以外のページでは「タイトル+URL」コピー
 * （title-url-bookmarklet と同じ動作）に自動フォールバックする。
 *
 * インストール: docs/index.html のリンクをブックマークバーへドラッグ&ドロップするか、
 * README.md 内のコードブロックをブックマークのURL欄に貼り付ける。
 *
 * 使い方: Box.comのファイル/フォルダ詳細ページ、または任意のページでクリックするだけ。
 *
 * 動作:
 *   1. document.URL が *.box.com でなければ、タイトル+URLコピーにフォールバックして終了。
 *   2. URL末尾（クエリを除く）をIDとして抽出。数字でなければ何もしない。
 *   3. URLに"folder"を含むかでファイル/フォルダを判定。
 *   4. ファイルの場合: Box内部API（app-api/enduserapp/item/f_<id>?format=preview）から
 *      ファイル名と親フォルダIDを取得。
 *   5. Box内部API（app-api/enduserapp/folder/<id>）でフォルダの祖先パスを取得し、
 *      ルート直下（マイファイル等）を除いた名前配列を作る。
 *   6. "/Box/親フォルダ/.../ファイル名" + 改行 + document.URL を
 *      navigator.clipboard.writeText でコピーする。
 *
 * 依存: グローバル `Box.prefetchedData["/app-api/enduserapp/current-user"]`
 * （Box Web UIがページ読み込み時に埋め込む内部データ）。Box側のUI/内部API変更で壊れる可能性がある。
 */
(function () {
  var global = window;
  global.COPY_TO_CLIPBOARD = global.COPY_TO_CLIPBOARD || {};

  global.COPY_TO_CLIPBOARD.confirmUrlExistence = function (url) {
    return fetch(url)
      .then(function (response) {
        if (!response.ok) { throw new Error(response.statusText); }
        return response.json();
      })
      .catch(function (error) { console.log('Fetch failed.', error); });
  };

  global.COPY_TO_CLIPBOARD.createTargetFolderPath = function (targetFolderId) {
    return Box.prefetchedData['/app-api/enduserapp/current-user'].preview.appHost
      + 'app-api/enduserapp/folder/' + targetFolderId;
  };

  global.COPY_TO_CLIPBOARD.createTargetFilePath = function (targetFileId) {
    return Box.prefetchedData['/app-api/enduserapp/current-user'].preview.appHost
      + 'app-api/enduserapp/item/f_' + targetFileId + '?format=preview';
  };

  global.COPY_TO_CLIPBOARD.getBoxFolderPath = function (json) {
    return json.folder.path.map(function (i) { return i.name; })
      .filter(function (_, idx) { return idx > 0; });
  };

  global.COPY_TO_CLIPBOARD.getUrlInfo = function () {
    var title = new String(document.title);
    title.allReplace = function (replacements) {
      var result = this, key;
      for (key in replacements) {
        result = result.replace(new RegExp(key, 'g'), replacements[key]);
      }
      return result;
    }.bind(title);
    return title.allReplace({ ':': '：', '\\[': '［', '\\]': '］' }) + '\n' + document.URL;
  };

  global.COPY_TO_CLIPBOARD.copyTextAndTitle = function () {
    navigator.clipboard.writeText(this.getUrlInfo());
  };

  global.COPY_TO_CLIPBOARD.copyToClipboard = async function () {
    if (!/^.*\.box\.com.*$/.test(document.URL)) {
      console.log('Does not work outside Box web.');
      this.copyTextAndTitle();
      return;
    }

    var fileId = document.URL.split('/').pop().split('?')[0];
    if (!/^\d+$/.test(fileId)) { return; }

    var isFolder = /folder/.test(document.URL);
    var fileName = null;
    var folderId = fileId;

    if (!isFolder) {
      var jsonFile = await this.confirmUrlExistence(this.createTargetFilePath(fileId));
      if (!jsonFile) { console.log('Failed to obtain file information.'); return; }
      fileName = jsonFile.items[0].name;
      folderId = jsonFile.items[0].parentFolderID;
    }

    var jsonFolder = await this.confirmUrlExistence(this.createTargetFolderPath(folderId));
    if (!jsonFolder) { console.log('Failed to obtain folder information.'); return; }

    var arrayFolder = this.getBoxFolderPath(jsonFolder);
    var joinFolderName = arrayFolder.join('/');
    var folderAndFileName = fileName !== null ? joinFolderName + '/' + fileName : joinFolderName;
    var boxPathStr = '/Box/' + folderAndFileName + '\n';
    var res = boxPathStr + document.URL;

    navigator.clipboard.writeText(res);
    console.log(res);
  };

  global.COPY_TO_CLIPBOARD.copyToClipboard();
})();
