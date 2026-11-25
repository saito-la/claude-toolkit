// AI 用 Chrome に、前面へ出さずにタブを1枚開き、targetId を出力する。
// 使い方: node new-tab.mjs <CDPポート> <URL>
// /json/new と Playwright の newPage は、開いた瞬間に Chrome を前面へ出してフォーカスを取る（2026-11-11 実測）。
// Target.createTarget に background: true を付けると、前面のアプリはそのまま残る。呼び出し口は claude-chrome.sh の tab。
const [port, url = 'about:blank'] = process.argv.slice(2);
if (!port) {
  console.error('usage: new-tab.mjs <port> [url]');
  process.exit(2);
}
const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
const ws = new WebSocket(webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
ws.send(JSON.stringify({ id: 1, method: 'Target.createTarget', params: { url, background: true } }));
const res = await new Promise(resolve => { ws.onmessage = e => { const d = JSON.parse(e.data); if (d.id === 1) resolve(d); }; });
ws.close();
if (res.error) {
  console.error(`Target.createTarget が失敗: ${res.error.message}`);
  process.exit(1);
}
console.log(res.result.targetId);
