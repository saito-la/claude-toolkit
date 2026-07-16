/**
 * Gmail: 現在開いているメールの Message-ID を取得してクリップボードにコピーするブックマークレット。
 *
 * インストール: 本ファイルをそのまま1行にminifyしたものを
 * ブックマークのURL欄に "javascript:(function(){...})();" として貼り付ける。
 * minify済みの貼り付け用一行は README.md 参照。
 *
 * 使い方: 通常のメール表示画面（「Show original」を開く前）でブックマークをクリックするだけ。
 * コピーされる文字列は "Message-ID: <...>" 形式（Claudeに貼ればそのままMessage-IDと分かる）。
 *
 * 動作:
 *   0. 既に「Show original」ページを開いている場合はそのまま本文からMessage-IDを抽出。
 *   1. 「⋮」(More message options) ボタンを自動クリックしてメニューを展開。
 *   2. メニュー内の "Show original" 項目をクリック。Gmailはこれを window.open() 経由で
 *      新規タブに開くため、window.open をフックして開いたタブの参照を保持する
 *      （fetch()での直接取得はGmail側の制限で失敗するため使わない）。
 *   3. 新規タブのDOMをポーリングして本文読み込みを待ち、Message-IDを抽出。
 *   4. 新規タブを閉じ、window.focus() で元タブにフォーカスを戻してから少し待って
 *      クリップボードに書き込む（フォーカスが無い状態でnavigator.clipboard.writeTextを
 *      呼ぶと失敗するため）。
 *
 * 依存しているGmail内部のDOM（UI変更で壊れる可能性あり。2026-07-11時点で動作確認済み）:
 *   - button[aria-label="More message options"]
 *   - [role="menuitem"] のテキストが "Show original"（英語UI前提。日本語UIなら文言変更が必要）
 */
(function () {
  function copyIt(id) {
    var labeled = 'Message-ID: <' + id + '>';
    var done = function () { alert('コピーしました:\n' + labeled); };
    var fail = function () { window.prompt('コピー失敗。手動でコピーしてください:', labeled); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(labeled).then(done, fail);
    } else {
      fail();
    }
  }

  function findId(text) {
    var m = text.match(/Message-ID:\s*<([^>]+)>/i);
    return m ? m[1] : null;
  }

  // 0) 既に Show original ページ上にいる場合
  var onPage = findId(document.body.innerText || document.body.textContent || '');
  if (onPage) { copyIt(onPage); return; }

  // 1) ⋮ ボタンをクリックしてメニューを開く
  var btn = document.querySelector('button[aria-label="More message options"]');
  if (!btn) {
    alert('操作ボタンが見つかりません。手動で「⋮」→「Show original」を開いてから実行してください。');
    return;
  }
  btn.click();

  setTimeout(function () {
    var items = Array.from(document.querySelectorAll('[role="menuitem"]'));
    var target = items.find(function (el) {
      return (el.textContent || '').trim() === 'Show original';
    });
    if (!target) {
      alert('メニューに「Show original」が見つかりませんでした。手動で開いてから実行してください。');
      return;
    }

    // window.open をフックして新規タブの参照を捕捉
    var capturedWin = null;
    var originalOpen = window.open;
    window.open = function () {
      capturedWin = originalOpen.apply(window, arguments);
      return capturedWin;
    };
    target.click();

    setTimeout(function () {
      window.open = originalOpen;
      if (!capturedWin) {
        alert('新しいタブを検出できませんでした。手動で「⋮」→「Show original」を開いてから実行してください。');
        return;
      }

      // 2) 新規タブのDOMをポーリングしてMessage-IDを待つ（最大 20 * 300ms ≈ 6秒）
      var tries = 0;
      var poll = setInterval(function () {
        tries++;
        var text = '';
        try {
          text = capturedWin.document.body
            ? (capturedWin.document.body.innerText || capturedWin.document.body.textContent || '')
            : '';
        } catch (e) { /* まだ読めない場合は無視して次のポーリングへ */ }

        var id = findId(text);
        if (id) {
          clearInterval(poll);
          try { capturedWin.close(); } catch (e) {}
          window.focus();
          // フォーカスが戻るまで少し待ってからクリップボード書き込み
          setTimeout(function () { copyIt(id); }, 200);
        } else if (tries > 20) {
          clearInterval(poll);
          try { capturedWin.close(); } catch (e) {}
          alert('新しいタブは開きましたがMessage-IDを検出できませんでした。');
        }
      }, 300);
    }, 300);
  }, 500);
})();
