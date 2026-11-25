// 走っている Chrome インスタンスの Dock と Cmd+Tab のアイコンを、CDP の Browser.setDockTile で差し替える。
// 使い方: node set-dock-tile.mjs <CDPポート> <PNG>
// 差し替えはそのプロセスが終わるまで有効。起動のたびに claude-chrome.sh start が呼ぶ（README.md「アイコン」）。
import { readFileSync } from 'node:fs';

const [port, png] = process.argv.slice(2);
if (!port || !png) {
  console.error('usage: set-dock-tile.mjs <port> <png>');
  process.exit(1);
}
const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
const ws = new WebSocket(webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
ws.send(JSON.stringify({ id: 1, method: 'Browser.setDockTile', params: { image: readFileSync(png).toString('base64') } }));
const res = await new Promise(resolve => { ws.onmessage = e => resolve(JSON.parse(e.data)); });
ws.close();
if (res.error) {
  console.error(`Browser.setDockTile が失敗: ${res.error.message}`);
  process.exit(1);
}
