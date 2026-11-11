#!/usr/bin/env node
/**
 * 前回の today.md の各項目について、その後に何が起きたかの材料を集め、項目ごとに印を付ける。
 *
 *   node <エンジン>/scripts/evidence.mjs [--since <ISO8601>]   # インスタンスのフォルダで実行する
 *
 * init・update で、Claude が項目ごとにメールや台帳を1件ずつ引き直す往復を、このスクリプト1回に置き換える。
 * 「変化なし」の項目は Claude が開かずにそのまま載せてよく、それ以外だけを読む（読む量が多ければ
 * Sonnet のサブエージェントに分ける）。設計は ../docs/daily-and-update.md。
 *
 * 起点は前回の today.md の commit の時刻。today.md を git に入れないインスタンスでは today.md を最後に書いた時刻
 * （--since で変えられる）。項目ごとに見るもの：
 *   - 台帳の行：参照している行が今も未処理か。違えば、当日の控え（data/today-refs.json）の本文で探し直す
 *   - メール：項目と台帳の行に書かれた Gmail の ID のスレッドで、起点より後に送った通・届いた通。
 *     台帳の行に書かれたアドレスとの、起点より後のやり取り（設定の gmail のアカウント。Gmail の設定が無ければメールは見ない）
 *   使い手の通か、送った・予約した・下書き・届いたのどれかは lib/gmail-kind.mjs で決める
 * メールを見る起点は項目ごとに決める。参照する台帳の行を最後に書いた時刻（git blame）が前回の today.md より前なら、
 * その時刻から見る。前回より前に済んでいたのに台帳が直っていない項目は、前回からの差分だけを見ると毎回「変化なし」に
 * なり、載り続ける（2026-11-08、11/4 08:31 に足された行に 08:38 の返信と 08:49 の続報があり、4日間「変化なし」で載った）。
 * 台帳の行を直せば起点も進むので、印が消える。
 * Gmail の ID もアドレスも持たない項目（台帳の行だけの項目を含む）は、送受信を機械で確かめられないので「鍵なし」を付け、「変化なし」と分ける。
 * 印は 閉じた・ずれた・送った・予約した・下書き・届いた・鍵なし・変化なし の組み合わせ。「下書き」は Gmail の未送信の下書きで、
 * 項目が求める送信が済んだ証拠にはならない。送るべき項目に「下書き」だけが付いていれば、まだ送っていない。
 * 結果は data/evidence.json にも書く。
 */
import { readFileSync, writeFileSync, existsSync, statSync } from 'fs';
import { execFileSync } from 'child_process';
import { dirname, basename } from 'path';
import { refsOf, PROJECTS } from './today-refs.mjs';
import { instance } from './lib/instance.mjs';
import { accessToken as tokenOf } from './lib/google.mjs';
import { header, myAddresses, kindOf } from './lib/gmail-kind.mjs';

const INST = instance();
const GMAIL = INST.cfg.gmail;
const argv = process.argv.slice(2);
const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv[i + 1]; };

const TODAY = INST.file('today.md');
if (!existsSync(TODAY)) { console.log('today.md がまだ無い。前回からの変化を見る項目は無い'); process.exit(0); }

// 前回の today.md の commit の時刻。git に入れていなければ、today.md を最後に書いた時刻
function lastWritten() {
  try {
    const ct = execFileSync('git', ['-C', INST.dir, 'log', '-1', '--format=%ct', '--', 'today.md'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (ct) return new Date(Number(ct) * 1000);
  } catch { /* git の外 */ }
  return statSync(TODAY).mtime;
}
const since = opt('--since') ? new Date(opt('--since')) : lastWritten();
const sinceMs = since.getTime();

// 項目（番号付き・箇条書きの行）
const lines = readFileSync(TODAY, 'utf8').split('\n');
let section = '';
const items = [];
lines.forEach((text) => {
  const h = text.match(/^## (.+)$/);
  if (h) { section = h[1].trim(); return; }
  if (/^(\d+\.|-) /.test(text)) items.push({ section, text });
});

let snap = {};
try { snap = JSON.parse(readFileSync(INST.file('data', 'today-refs.json'), 'utf8')).rows || {}; } catch {}

// Gmail の設定が無ければ、メールは見ずに台帳の行だけを見る
const token = GMAIL ? await tokenOf(GMAIL.credential) : null;
const get = async (path) => {
  if (!token) return null;
  const r = await fetch(`https://gmail.googleapis.com/gmail/v1/users/me/${path}`, { headers: { Authorization: `Bearer ${token}` } });
  return r.ok ? r.json() : null;
};
const myAddrs = await myAddresses(get);
const when = (m) => new Date(Number(m.internalDate)).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
// 判定は lib/gmail-kind.mjs。下書きは未送信なので、送った証拠ではないことを notes にも書く
const kind = (m) => kindOf(m, myAddrs);
const brief = (m) => `${when(m)} ${kind(m) === '下書き' ? '下書き（未送信。送った証拠ではない）' : kind(m)}「${header(m, 'Subject').slice(0, 30)}」 ${m.id}`;
const META = 'format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=To';

// 台帳の行を最後に書いた時刻（commit の時刻）。git の外・未 commit の行は null
function lineWritten(file, n) {
  try {
    const out = execFileSync('git', ['-C', dirname(file), 'blame', '--porcelain', '-L', `${n},${n}`, '--', basename(file)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    if (/^0{40} /.test(out)) return null;
    const t = out.match(/^committer-time (\d+)$/m);
    return t ? Number(t[1]) * 1000 : null;
  } catch { return null; }
}

const threadCache = new Map();
async function laterInThread(id, startMs) {
  if (!threadCache.has(id)) {
    const msg = await get(`messages/${id}?format=minimal`);
    const th = msg && await get(`threads/${msg.threadId}?${META}`);
    threadCache.set(id, th?.messages || []);
  }
  return threadCache.get(id).filter((m) => Number(m.internalDate) > startMs);
}
const addrCache = new Map();
async function laterWith(addr, startMs) {
  const key = `${addr} ${startMs}`;
  if (!addrCache.has(key)) {
    const q = encodeURIComponent(`{from:${addr} to:${addr} cc:${addr}} after:${Math.floor(startMs / 1000)}`);
    const list = (await get(`messages?q=${q}&maxResults=5`))?.messages || [];
    addrCache.set(key, (await Promise.all(list.map((m) => get(`messages/${m.id}?${META}`)))).filter(Boolean));
  }
  return addrCache.get(key);
}

const out = [];
for (const it of items) {
  const marks = new Set();
  const notes = [];
  const keys = { ids: new Set([...it.text.matchAll(/#all\/([0-9a-f]{16})/g)].map((m) => m[1])), addrs: new Set() };
  let startMs = sinceMs;
  const refs = refsOf(it.text);
  for (const { path, n } of refs) {
    const file = `${PROJECTS}/${path}`;
    if (!existsSync(file)) { marks.add('閉じた'); notes.push(`${path} が無い`); continue; }
    const rows = readFileSync(file, 'utf8').split(/\r?\n/);
    let row = rows[n - 1] || '';
    const pinned = snap[`${path}:${n}`];
    if (pinned !== undefined && row !== pinned) {
      const moved = rows.findIndex((r) => r === pinned);
      if (moved === -1) { marks.add('閉じた'); notes.push(`${path}:${n} の行が無くなった`); continue; }
      marks.add('ずれた'); notes.push(`${path}:${n} → ${moved + 1}`); row = rows[moved];
    } else if (!/^\s*- \[[ !]\]/.test(row)) { marks.add('閉じた'); notes.push(`${path}:${n} は未処理の行ではない`); continue; }
    const at = lineWritten(file, rows.indexOf(row) + 1);
    if (at && at < startMs) { startMs = at; notes.push(`起点を台帳の行を書いた ${new Date(at).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} に戻した`); }
    for (const m of row.matchAll(/(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])/g)) keys.ids.add(m[1]);
    for (const m of row.matchAll(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g)) keys.addrs.add(m[0].toLowerCase());
  }
  const seen = new Map();
  for (const id of keys.ids) for (const m of await laterInThread(id, startMs)) seen.set(m.id, m);
  for (const a of keys.addrs) if (!myAddrs.includes(a)) for (const m of await laterWith(a, startMs)) seen.set(m.id, m);
  for (const m of [...seen.values()].sort((a, b) => a.internalDate - b.internalDate)) {
    marks.add(kind(m));
    notes.push(brief(m));
  }
  if (!marks.size) marks.add(keys.ids.size || keys.addrs.size ? '変化なし' : '鍵なし');
  out.push({ section: it.section, text: it.text.replace(/\s*\[[^\]]+\]\(pj:\/\/[^)]*\)/g, '').slice(0, 80), marks: [...marks], notes });
}

writeFileSync(INST.file('data', 'evidence.json'), JSON.stringify({ since: since.toISOString(), items: out }, null, 1));
const calm = out.filter((x) => x.marks[0] === '変化なし');
const keyless = out.filter((x) => x.marks[0] === '鍵なし');
if (!token) console.log('Gmail の設定が無いので、メールの送受信は見ていない。台帳の行だけを見た');
console.log(`起点 ${since.toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' })} から。${out.length} 項目のうち変化なし ${calm.length}、鍵なし ${keyless.length}、読む必要あり ${out.length - calm.length - keyless.length}`);
for (const x of out.filter((x) => !['変化なし', '鍵なし'].includes(x.marks[0]))) {
  console.log(`[${x.marks.join('・')}]（${x.section}）${x.text}`);
  for (const n of x.notes.slice(0, 6)) console.log(`    ${n}`);
}
