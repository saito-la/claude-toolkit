// AI 用 Chrome の後始末の共通の口。自分が開いたタブを閉じ、そのインスタンスに「中身のあるタブ」が
// 1枚も残らなければ Chrome ごと終了する（instructions/tools.md「ブラウザのタブの後始末」の決まりを、スクリプトが必ず通る形にしたもの）。
//
// 呼び出し口:
//   claude-chrome [--<呼び名>] release [--match <正規表現>] [targetId...]
//   import { releaseChrome } from '<このファイル>'   （Playwright を使うスクリプトから）
//
// 閉じる対象は、渡された targetId と、URL が --match に当たるタブだけ。人が開いたタブ・ほかの処理のタブには触れない。
// 終了するのは、閉じたあとに残るタブがすべて空（about:blank・新しいタブ）のときだけ。人やほかの処理のタブが残っていれば終了しない。
// 空タブは claude-chrome の start が「タブ0枚だと connectOverCDP が失敗する」対策で自分で立てるものなので、残りに数えない。
//
// 限界: 判定から終了までの間に、ほかの処理が新しいタブを開く競合は防げない（隙間は数百 ms）。
//   終了しても、ログインはプロファイルに残るので、次の処理の start で戻る。
//
// connectOverCDP の browser.close() は切断だけで、タブも Chrome も残る。後始末はこの口を呼ぶ側が行う。
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const EMPTY_TAB = /^(about:blank|(chrome|edge):\/\/(newtab|new-tab-page)\/?)$/; // Edge の新しいタブは edge://newtab
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pages(port) {
  const r = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(3000) });
  return (await r.json()).filter((t) => t.type === 'page');
}

async function quit(port) {
  const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const ws = new WebSocket(webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.send(JSON.stringify({ id: 1, method: 'Browser.close' }));
  await new Promise((resolve) => { ws.onclose = resolve; ws.onmessage = resolve; setTimeout(resolve, 3000); });
}

/**
 * @param {object} o
 * @param {string|number} o.port CDP のポート
 * @param {string[]} [o.targetIds] 閉じるタブ
 * @param {RegExp|string|null} [o.match] URL がこれに当たるタブも閉じる
 * @param {boolean} [o.quit=true] false なら、タブを閉じるだけで Chrome は終了しない
 * @returns {Promise<{closed: number, remaining: string[], quit: boolean}>}
 */
export async function releaseChrome({ port, targetIds = [], match = null, quit: doQuit = true }) {
  const re = match ? new RegExp(match) : null;
  let list;
  try { list = await pages(port); } catch { return { closed: 0, remaining: [], quit: false }; } // 起動していない
  const mine = list.filter((t) => targetIds.includes(t.id) || (re && re.test(t.url)));
  for (const t of mine) await fetch(`http://127.0.0.1:${port}/json/close/${t.id}`).catch(() => {});
  if (mine.length) await sleep(300);
  const left = (await pages(port).catch(() => [])).filter((t) => !mine.some((m) => m.id === t.id));
  const real = left.filter((t) => !EMPTY_TAB.test(t.url));
  if (!doQuit || real.length) return { closed: mine.length, remaining: real.map((t) => t.url), quit: false };
  await quit(port).catch(() => {});
  return { closed: mine.length, remaining: [], quit: true };
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const args = process.argv.slice(2);
  const port = args.shift();
  let match = null;
  const ids = [];
  while (args.length) {
    const a = args.shift();
    if (a === '--match') match = args.shift();
    else ids.push(a);
  }
  if (!port) { console.error('usage: release.mjs <port> [--match <regex>] [targetId...]'); process.exit(2); }
  const r = await releaseChrome({ port, targetIds: ids, match });
  console.log(r.quit ? `タブ ${r.closed} 枚を閉じ、Chrome を終了しました` : `タブ ${r.closed} 枚を閉じました（残り ${r.remaining.length} 枚のため Chrome は終了しません）`);
}
