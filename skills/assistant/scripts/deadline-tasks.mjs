#!/usr/bin/env node
/**
 * deadline-tasks.mjs — today.md の「直近の締切」「先の締切」の行を、Google Tasks のタスクとして予定表に載せる
 *
 * 外部から決められた期日だけを載せる（docs/design.md「期日を予定表に載せる」）。締切の節に何を載せるかは
 * today.md を書くときに判断済みなので、ここでは選び直さない。タスクは持ち主にしか見えず、共有・公開している
 * 予定表には出ない。2w Schedule には doc-2w.mjs がこのタスクを「締切」の行として各日の先頭に入れる。
 *
 * Usage（インスタンスのフォルダで実行する）:
 *   node deadline-tasks.mjs           # dry-run（足す・閉じるタスクを表示するだけ）
 *   node deadline-tasks.mjs --apply
 *
 * 挙動：
 *   - 同じ期日・同じ台帳の行のタスクが既にあれば足さない（完了済みも含む。使い手が予定表で完了にしたものを戻さない）
 *   - このスクリプトが作ったタスク（notes に assistant-key の行がある）のうち、締切の節から消えたものを完了にする。
 *     期日を過ぎたものは、today.md のどこか（【期限超過】）がまだ同じ台帳の行を指していれば残す
 *   - 手で作ったタスクには触らない。消さない（完了にするだけ）
 *   - today.md の見出しの日付が今日でなければ止まる（古い一覧で閉じてしまわないため）
 *
 * 設定は deadlineTasks（credential が無ければ何もしない）。README.md「3. 設定」。
 */
import { readFileSync } from 'fs';
import { instance } from './lib/instance.mjs';
import { refsOf } from './today-refs.mjs';
import { tasksToken, findList, listTasks, insertTask, completeTask, dueOf, keyOf, KEY_PREFIX } from './lib/tasks.mjs';

const INST = instance();
const CFG = INST.cfg.deadlineTasks;
const APPLY = process.argv.includes('--apply');
const SECTIONS = ['直近の締切', '先の締切'];

if (!CFG?.credential) {
  console.log('deadlineTasks.credential が無いので、締切をタスクに載せない（README.md「3. 設定」）');
  process.exit(0);
}

const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
// 年の無い M/D には、今日から前後半年で最も近い年を補う（collect.mjs と同じ読み方）
function ymdOf(mo, d) {
  const ty = Number(today.slice(0, 4));
  return [ty - 1, ty, ty + 1].map((y) => `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`)
    .sort((a, b) => Math.abs(daysBetween(today, a)) - Math.abs(daysBetween(today, b)))[0];
}
const clean = (t) => t.replace(/\[([^\]]*)\]\([^)]*\)/g, '').replace(/‹[^›]*›/g, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();

// ── today.md の締切の節を読む ─────────────────────────────
const text = readFileSync(INST.file('today.md'), 'utf8');
const head = text.match(/^# .*?(\d{4}-\d{2}-\d{2})/m)?.[1];
if (head !== today) {
  console.error(`❌ today.md の日付（${head ?? '読めない'}）が今日（${today}）でない。今日の today.md を作ってから流す`);
  process.exit(1);
}
const wanted = [];
let section = '';
for (const line of text.split('\n')) {
  const h = line.match(/^## (.+)$/);
  if (h) { section = h[1].trim(); continue; }
  if (!SECTIONS.includes(section)) continue;
  // 「- 11/12（木）正午 — 〇〇を出す [台帳](pj://…)」
  const m = line.match(/^- (\d{1,2})\/(\d{1,2})（[^）]*）\s*([^—]*?)\s*—\s*(.+)$/);
  if (!m) continue;
  const due = ymdOf(+m[1], +m[2]);
  const action = clean(m[4]);
  const title = m[3] ? `${action}（${m[3]}）` : action;
  const ref = refsOf(line)[0];
  // 鍵は台帳のパスと期日。行番号は台帳の編集でずれ、文言は毎日書き直されるので鍵にしない
  const key = ref ? `${ref.path}@${due}` : `${due} ${action.slice(0, 30)}`;
  // 同じ台帳・同じ期日の行が2つ以上あれば1件のタスクにまとめる
  const same = wanted.find((w) => w.key === key);
  if (same) same.title += ` ／ ${title}`;
  else wanted.push({ due, title, key, ref });
}
const refsInToday = new Set(refsOf(text).map((r) => r.path));

// ── タスクと突き合わせる ─────────────────────────────────
const token = await tasksToken(CFG.credential);
const listId = await findList(token, CFG.list, { create: APPLY });
const tasks = listId ? await listTasks(token, listId) : [];
const byKey = new Map(tasks.filter(keyOf).map((t) => [keyOf(t), t]));

const toAdd = wanted.filter((w) => !byKey.has(w.key) && w.due >= today);
const wantedKeys = new Set(wanted.map((w) => w.key));
const toClose = tasks.filter((t) => keyOf(t) && t.status !== 'completed' && !wantedKeys.has(keyOf(t)))
  .filter((t) => dueOf(t) >= today || !refsInToday.has(keyOf(t).split('@')[0]));

console.log(`📌 締切の節 ${wanted.length} 行 / タスクリスト「${CFG.list}」${listId ? `${tasks.length} 件` : '（まだ無い）'}`);
for (const w of toAdd) console.log(`  + ${w.due} ${w.title}`);
for (const t of toClose) console.log(`  ✓ ${dueOf(t)} ${t.title}（締切の節から消えたので完了にする）`);
if (!toAdd.length && !toClose.length) { console.log('✅ 変更なし'); process.exit(0); }
if (!APPLY) { console.log('\n（dry-run。書き込むには --apply）'); process.exit(0); }

for (const w of toAdd) {
  const notes = [
    w.ref ? `台帳：${w.ref.path}:${w.ref.n}` : null,
    `${KEY_PREFIX}${w.key}`,
    '（assistant の deadline-tasks.mjs が today.md の締切の節から作った）',
  ].filter(Boolean).join('\n');
  await insertTask(token, listId, { title: w.title, notes, due: w.due });
}
for (const t of toClose) await completeTask(token, listId, t.id);
console.log(`✅ タスクを ${toAdd.length} 件足し、${toClose.length} 件を完了にした`);
