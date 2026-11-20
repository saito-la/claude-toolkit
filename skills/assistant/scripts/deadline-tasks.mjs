#!/usr/bin/env node
/**
 * deadline-tasks.mjs — today.md に載った外部の期日を、Google Tasks のタスクとして予定表に載せる
 *
 * 外部から決められた期日だけを載せる（docs/design.md「期日を予定表に載せる」）。何を today.md に載せるかは
 * today.md を書くときに判断済みなので、ここでは選び直さない。タスクは持ち主にしか見えず、共有・公開している
 * 予定表には出ない。2w Schedule には doc-2w.mjs がこのタスクを「締切」の行として各日の先頭に入れる。
 *
 * Usage（インスタンスのフォルダで実行する）:
 *   node deadline-tasks.mjs                   # dry-run（足す・閉じるタスクを表示するだけ）
 *   node deadline-tasks.mjs --apply
 *   node deadline-tasks.mjs --reopen [--apply] # 完了済みのうち today.md がまだ指しているものを未完了に戻す
 *
 * 足すもの：
 *   - 「直近の締切」「先の締切」の行
 *   - 「やること」「2分で済むこと」の項目のうち、参照する台帳の行に外部の期日があるもの（data/candidates.json の ledgers.upcoming）。
 *     締切の節は「やること」と重なる行を載せないので、優先度が上がって「やること」へ移った期日もここで拾う
 *   - 同じ期日・同じ台帳の行のタスクが既にあれば足さない（完了済みも含む。使い手が予定表で完了にしたものを戻さない）
 * 完了にするもの：
 *   - このスクリプトが作ったタスク（notes に assistant-key の行がある）のうち、today.md のどの節からも参照が消えたもの。
 *     参照が残っているかは、today.md の台帳の参照（行）を candidates.json で期日に引き、鍵（台帳のパス@期日）で照らす。
 *     「やること」へ移った行、【期限超過】で残る行、ほかの節に移った行は残す
 *   - candidates.json が今日のものでなければ、行を期日に引けないので、同じ台帳がどこかで参照されていれば残す
 *   - 手で作ったタスクには触らない。消さない（完了にするだけ）
 *   - today.md の見出しの日付が今日でなければ止まる（古い一覧で閉じてしまわないため）
 * --reopen は、締切の節しか見ていなかった版（2026-11-08〜11-18）が「やること」へ移った行を完了にしたものを戻すためにある。
 * 使い手が予定表で完了にしたタスクとは区別できないので、dry-run の一覧（完了にした時刻つき）を見て使い手が決める。
 *
 * 設定は deadlineTasks（credential が無ければ何もしない）。README.md「3. 設定」。
 */
import { readFileSync, existsSync } from 'fs';
import { instance } from './lib/instance.mjs';
import { refsOf, TODO_SECTIONS } from './today-refs.mjs';
import { tasksToken, findList, listTasks, insertTask, completeTask, reopenTask, dueOf, keyOf, KEY_PREFIX } from './lib/tasks.mjs';

const INST = instance();
const CFG = INST.cfg.deadlineTasks;
const APPLY = process.argv.includes('--apply');
const REOPEN = process.argv.includes('--reopen');
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
// 「やること」の行から題名を作る。番号・【期限超過】などの札・見積（約10分）・句点を外す
const titleOfTodo = (line) => clean(line).replace(/^(\d+\.|-)\s*/, '').replace(/【[^】]*】/g, '').replace(/（約[^）]*）/g, '').replace(/。$/, '').trim();

// ── today.md を読む ─────────────────────────────────────
const text = readFileSync(INST.file('today.md'), 'utf8');
const head = text.match(/^# .*?(\d{4}-\d{2}-\d{2})/m)?.[1];
if (head !== today) {
  console.error(`❌ today.md の日付（${head ?? '読めない'}）が今日（${today}）でない。今日の today.md を作ってから流す`);
  process.exit(1);
}

// 台帳の行 → 外部の期日。candidates.json が今日のものでなければ null（行を期日に引けない）
let dueOfLine = null;
const candPath = INST.file('data', 'candidates.json');
if (existsSync(candPath)) {
  const c = JSON.parse(readFileSync(candPath, 'utf8'));
  if (c.today === today) {
    dueOfLine = new Map();
    for (const r of [...(c.ledgers?.overdue ?? []), ...(c.ledgers?.upcoming ?? [])]) {
      if (r.deadline?.kind === 'external') dueOfLine.set(`${r.path}:${r.line}`, r.deadline.ymd);
    }
  }
}
if (!dueOfLine) console.log(`⚠ data/candidates.json が今日（${today}）のものでない。「やること」の期日は足さず、台帳がどこかで参照されている限り閉じない`);

const wanted = [];
const add = (w) => {
  // 同じ台帳・同じ期日の行が2つ以上あれば1件のタスクにまとめる
  const same = wanted.find((x) => x.key === w.key);
  if (!same) wanted.push(w);
  else if (!same.title.includes(w.title)) same.title += ` ／ ${w.title}`;
};
const liveKeys = new Set(); // today.md のどこかが指している鍵
const refPaths = new Set(); // today.md のどこかが指している台帳
let section = '';
for (const line of text.split('\n')) {
  const h = line.match(/^## (.+)$/);
  if (h) { section = h[1].trim(); continue; }
  const refs = refsOf(line);
  for (const r of refs) {
    refPaths.add(r.path);
    const due = dueOfLine?.get(`${r.path}:${r.n}`);
    if (!due) continue;
    const key = `${r.path}@${due}`;
    liveKeys.add(key);
    if (TODO_SECTIONS.includes(section) && due >= today) add({ due, title: titleOfTodo(line), key, ref: r });
  }
  if (!SECTIONS.includes(section)) continue;
  // 「- 11/12（木）正午 — 〇〇を出す [台帳](pj://…)」
  const m = line.match(/^- (\d{1,2})\/(\d{1,2})（[^）]*）\s*([^—]*?)\s*—\s*(.+)$/);
  if (!m) continue;
  const ref = refs[0];
  // 台帳に年付きで書いた期日があればその年を採る（半年より先の M/D を前の年に読まないため）
  const md = `${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`;
  const fromLedger = ref && dueOfLine?.get(`${ref.path}:${ref.n}`);
  const due = fromLedger?.slice(5) === md ? fromLedger : ymdOf(+m[1], +m[2]);
  const action = clean(m[4]);
  // 鍵は台帳のパスと期日。行番号は台帳の編集でずれ、文言は毎日書き直されるので鍵にしない
  const key = ref ? `${ref.path}@${due}` : `${due} ${action.slice(0, 30)}`;
  liveKeys.add(key);
  add({ due, title: m[3] ? `${action}（${m[3]}）` : action, key, ref });
}
// 鍵が today.md のどこかに残っているか。candidates.json が古ければ、台帳がどこかで参照されていれば残っているとみなす
const isLive = (key) => liveKeys.has(key) || (!dueOfLine && refPaths.has(key.split('@')[0]));

// ── タスクと突き合わせる ─────────────────────────────────
const token = await tasksToken(CFG.credential);
const listId = await findList(token, CFG.list, { create: APPLY && !REOPEN });
const tasks = listId ? await listTasks(token, listId) : [];
const ours = tasks.filter(keyOf);

if (REOPEN) {
  const toReopen = ours.filter((t) => t.status === 'completed' && dueOf(t) >= today && isLive(keyOf(t)));
  console.log(`📌 完了済みのうち today.md がまだ指しているもの ${toReopen.length} 件`);
  for (const t of toReopen) console.log(`  ↺ ${dueOf(t)} ${t.title}（完了 ${t.completed ?? '?'}）`);
  if (!toReopen.length) process.exit(0);
  if (!APPLY) { console.log('\n（dry-run。戻すには --reopen --apply）'); process.exit(0); }
  for (const t of toReopen) await reopenTask(token, listId, t.id);
  console.log(`✅ ${toReopen.length} 件を未完了に戻した`);
  process.exit(0);
}

const byKey = new Map(ours.map((t) => [keyOf(t), t]));
const toAdd = wanted.filter((w) => !byKey.has(w.key) && w.due >= today);
const toClose = ours.filter((t) => t.status !== 'completed' && !isLive(keyOf(t)));

console.log(`📌 載せる期日 ${wanted.length} 件 / タスクリスト「${CFG.list}」${listId ? `${tasks.length} 件` : '（まだ無い）'}`);
for (const w of toAdd) console.log(`  + ${w.due} ${w.title}`);
for (const t of toClose) console.log(`  ✓ ${dueOf(t)} ${t.title}（today.md のどこからも参照が消えたので完了にする）`);
if (!toAdd.length && !toClose.length) { console.log('✅ 変更なし'); process.exit(0); }
if (!APPLY) { console.log('\n（dry-run。書き込むには --apply）'); process.exit(0); }

for (const w of toAdd) {
  const notes = [
    w.ref ? `台帳：${w.ref.path}:${w.ref.n}` : null,
    `${KEY_PREFIX}${w.key}`,
    '（assistant の deadline-tasks.mjs が today.md から作った）',
  ].filter(Boolean).join('\n');
  await insertTask(token, listId, { title: w.title, notes, due: w.due });
}
for (const t of toClose) await completeTask(token, listId, t.id);
console.log(`✅ タスクを ${toAdd.length} 件足し、${toClose.length} 件を完了にした`);
