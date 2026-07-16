# gmail-message-id-bookmarklet

Gmailで開いているメールの Message-ID を1クリックで取得し、`Message-ID: <...>` の形でクリップボードにコピーするブックマークレット。Claude Codeに「このメールを処理して」と伝える際、`rfc822msgid:` 検索用の一意な識別子として貼り付けられる。

## インストール

1. ブックマークバーで右クリック→「ページを追加」等で新規ブックマークを作成
2. 名前は任意（例「MsgID取得」）
3. URL欄に以下を貼り付けて保存

```
javascript:(function(){function copyIt(id){var labeled='Message-ID: <'+id+'>';var done=function(){alert('コピーしました:\n'+labeled);};var fail=function(){window.prompt('コピー失敗。手動でコピーしてください:',labeled);};if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(labeled).then(done,fail);}else{fail();}}function findId(text){var m=text.match(/Message-ID:\s*<([^>]+)>/i);return m?m[1]:null;}var onPage=findId(document.body.innerText||document.body.textContent||'');if(onPage){copyIt(onPage);return;}var btn=document.querySelector('button[aria-label="More message options"]');if(!btn){alert('操作ボタンが見つかりません。手動で「⋮」→「Show original」を開いてから実行してください。');return;}btn.click();setTimeout(function(){var items=Array.from(document.querySelectorAll('[role="menuitem"]'));var target=items.find(function(el){return (el.textContent||'').trim()==='Show original';});if(!target){alert('メニューに「Show original」が見つかりませんでした。手動で開いてから実行してください。');return;}var capturedWin=null;var originalOpen=window.open;window.open=function(){capturedWin=originalOpen.apply(window,arguments);return capturedWin;};target.click();setTimeout(function(){window.open=originalOpen;if(!capturedWin){alert('新しいタブを検出できませんでした。手動で「⋮」→「Show original」を開いてから実行してください。');return;}var tries=0;var poll=setInterval(function(){tries++;var text='';try{text=capturedWin.document.body?(capturedWin.document.body.innerText||capturedWin.document.body.textContent||''):'';}catch(e){}var id=findId(text);if(id){clearInterval(poll);try{capturedWin.close();}catch(e){}window.focus();setTimeout(function(){copyIt(id);},200);}else if(tries>20){clearInterval(poll);try{capturedWin.close();}catch(e){}alert('新しいタブは開きましたがMessage-IDを検出できませんでした。');}},300);},300);},500);})();
```

読みやすい元ソース（コメント付き）は [`gmail-message-id-bookmarklet.js`](gmail-message-id-bookmarklet.js)。

## 使い方

普通のメール表示画面（「Show original」を開く前の状態）でこのブックマークをクリックするだけ。「コピーしました」のアラートが出れば完了。`Message-ID: <...>` の形でクリップボードに入っているので、そのままClaude Codeへのプロンプトに貼り付ければ一意なメール識別子として使える（Gmail MCP等の `rfc822msgid:` 検索や `search_emails` の入力に利用できる）。

## 仕組み

1. 「⋮」(More message options) ボタンをJSで自動クリックしてメニューを展開
2. メニュー内の "Show original" 項目をクリック（Gmailは`window.open()`経由で新規タブを開く）
3. `window.open` を一時的にフックして開いたタブの参照を捕捉
4. 新規タブのDOMを300ms間隔でポーリングし、本文読み込み完了後にMessage-IDを正規表現抽出
5. 新規タブを閉じ、`window.focus()`で元タブにフォーカスを戻してから200ms待ってクリップボードに書き込み

## 制約・既知の脆弱性

- 英語UI（"Show original"表記）前提。日本語UI等では `[role="menuitem"]` のテキスト一致条件を変更する必要がある
- `button[aria-label="More message options"]` 等、Gmail内部のDOM構造に依存しており、Gmail側のUI変更で壊れる可能性がある。壊れた場合はDevTools Consoleでエラーを確認しつつ本ファイルを更新する
- Chrome 136以降、CDP経由でデフォルトプロファイルのライブセッションに接続する自動化は動作しない（`--remote-debugging-port`がデフォルトプロファイルで無効化されているため）。本ブックマークレットはページ内JS実行のみで完結するためこの制約を受けない
