/**
 * 現在開いているページの「タイトル + 改行 + URL」をクリップボードにコピーするブックマークレット。
 *
 * インストール: docs/index.html のリンクをブックマークバーへドラッグ&ドロップするか、
 * README.md 内のコードブロックをブックマークのURL欄に貼り付ける。
 *
 * 使い方: 任意のページでこのブックマークをクリックするだけ。
 * タイトル中の `:` `[` `]` は全角（：［］）に自動置換される
 * （Markdown等に貼り付けたときの記法事故を防ぐため）。
 *
 * 動作:
 *   1. document.title を取得し、`:` `[` `]` を全角文字に置換。
 *   2. 置換後のタイトル + 改行 + document.URL を組み立てる。
 *   3. 非表示のtextareaをbodyに追加して選択し、document.execCommand("copy")でコピー。
 *      navigator.clipboard.writeText ではなく execCommand を使うことで、
 *      クリップボード権限プロンプトなしに動作する。
 */
(function () {
  var global = window;
  global.COPY_TO_CLIPBOARD = global.COPY_TO_CLIPBOARD || {};

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

  global.COPY_TO_CLIPBOARD.copyToClipboard = function () {
    var textarea = document.createElement('textarea');
    textarea.textContent = this.getUrlInfo();
    var body = document.getElementsByTagName('body')[0];
    body.appendChild(textarea);
    textarea.select();
    var ok = document.execCommand('copy');
    body.removeChild(textarea);
    return ok;
  };

  global.COPY_TO_CLIPBOARD.copyToClipboard();
})();
