#!/usr/bin/env node
/**
 * today.md の各項目が、まだ済んでいないかを一次データで点検する。
 *
 *   node <エンジン>/scripts/check-today.mjs [today.md] [--fix]   # インスタンスのフォルダで実行する
 *
 * 手順の正本は ../docs/design.md「1件ずつの完了確認」。ここはその機械で拾える部分だけを受け持つ。
 *   1. 「やること」の各項目に出所（台帳のパスと行、Gmail のリンク、作業用の文書、時刻、top-priority.md）があるか。
 *      出所の無い項目は、済んだかどうかを誰も確かめられない
 *   2. 「`<台帳>` N行」の参照先が、今も未処理の行（`- [ ]`・`- [!]`）か。閉じた・消えた・ずれた行を拾う
 *   3. Gmail のリンクを付けた項目について、リンク先より後に同じスレッドで使い手が送った通（👍 などのリアクションと
 *      予約送信を含む）や、相手からの続報が無いか。送っていれば済んでいる可能性が高い。
 *      項目の本文にその送信・続報の時刻（HH:MM）を書いてあれば、確かめ済みとして扱う
 *   4. 台帳の未処理行のうち期限がこれから来るもの（candidates.json の ledgers.upcoming）が、today.md のどこかで
 *      参照されているか。遠い期限も落とさない（docs/design.md「期限の扱い」。2026-10-24 決定）。
 *      自分で決めた日付（目安、kind が self。ledgers.aims）は点検しない（2026-11-08 方針）
 *   6. 業務量：「やること」の見積の合計が上限（candidates.json の capacity）に収まるか
 *   7. 意識しておく案件：節が「やること」の直下にあり、keep-in-mind.md の全案件が載っているか
 *   5. 今日の一覧の書き方（../docs/design.md「今日の一覧の書き方」）：1 action（「やること」の項目の下の行、
 *      「直近の締切」「先の締切」の各行）が80文字以内（リンクを除く）か、「未確認」「送信済み」の類の語が無いか、
 *      「やること N」への参照だけの行が無いか。「他人ボール」の節の各行も80文字以内・「未確認」等なし
 *   6. 台帳の未処理行が引く Gmail の受信のうち、受信トレイに残っていて使い手宛（To）に届いてその後同じスレッドで使い手が送っていないもの
 *      （candidates.json の ledgers.ballReturned）が、today.md のどこかで参照されているか。期限の無い行が
 *      「Gmail inbox」の節（台帳に行があるので外す）と「やること」（期限が無いので上がらない）の間に落ちるのを止める
 * 送信がスレッドの外（別の件名・口頭）で行われた場合は拾えない。機械で通っても完了確認の代わりにはならない。
 * 問題があれば終了コード1を返す。
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { refsOf } from './today-refs.mjs';
import { instance, PROJECTS } from './lib/instance.mjs';
import { accessToken as tokenOf } from './lib/google.mjs';
import { header, myAddresses, kindOf } from './lib/gmail-kind.mjs';

const INST = instance();
const target = process.argv.slice(2).find((a) => !a.startsWith('--')) || INST.file('today.md');
const GMAIL = INST.cfg.gmail;
const accessToken = () => tokenOf(GMAIL.credential);

// 節ごとに行を分ける
const lines = readFileSync(target, 'utf8').split('\n');
let section = '';
const items = [];
lines.forEach((text, i) => {
  const h = text.match(/^## (.+)$/);
  if (h) { section = h[1].trim(); return; }
  if (/^(\d+\.|-) /.test(text)) items.push({ section, line: i + 1, text });
});

const problems = [];
const notes = [];
const flag = (it, msg) => problems.push(`  ${it.line}行（${it.section}）: ${msg}`);

// 1. 出所
const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const SOURCE = new RegExp(`pj:\\/\\/open|‹[^›]+›|action-items\\.md|\`[^\`]+\\.md\`|#all\\/[0-9a-f]+|${esc(INST.cfg.workDoc.name)}|top-priority\\.md|\\d{1,2}:\\d{2}`);
for (const it of items.filter((x) => x.section === 'やること')) {
  if (!SOURCE.test(it.text)) flag(it, `出所が無い。台帳のパスと行、メールのリンク、${INST.cfg.workDoc.name} のいずれかを添える`);
}

// 5. 今日の一覧の書き方。1 action は80文字以内（リンク・下書きの印を除く）
const ACTION_MAX = 80;
const stripLinks = (t) => t.replace(/\[([^\]]*)\]\([^)]*\)/g, '').replace(/https?:\/\/\S+/g, '').replace(/‹[^›]*›/g, '').trim();
{
  let sec = '';
  lines.forEach((text, i) => {
    const h = text.match(/^## (.+)$/);
    if (h) { sec = h[1].trim(); return; }
    const top = /^(\d+\.|-) /.test(text);
    const sub = /^\s+(-|\d+\.) /.test(text);
    const isAction = (sec === 'やること' && sub) || ((sec === '直近の締切' || sec === '先の締切' || sec === '他人ボール') && (top || sub));
    if (!isAction) return;
    const it = { line: i + 1, section: sec };
    const body = stripLinks(text.replace(/^\s*(-|\d+\.)\s+/, ''));
    if (body.length > ACTION_MAX) flag(it, `1 action が${body.length}文字（${ACTION_MAX}文字以内）。短く切って行を分ける`);
    if (/未確認/.test(body)) flag(it, '「未確認」と書かず、「〜を確かめる」という action にする');
    if (/送信済み|予約送信済み|が決まった|済み$/.test(body)) flag(it, '済んだことは書かない（経緯は載せず、残る action だけを書く）');
    if (/^(.{0,12}—\s*)?(「?やること\s*\d+」?\s*(を参照|参照|と同じ|へ)?|→?\s*やること\s*\d+)[。\s]*$/.test(body)) flag(it, '「やること N」への参照だけの行は置かない');
  });
}

// 2. 台帳の行（全節）。読み方（下書きの印・pj リンク・以前の書き方）は today-refs.mjs
function resolve(p) {
  for (const base of [PROJECTS, INST.dir]) if (existsSync(`${base}/${p}`)) return `${base}/${p}`;
  return null;
}
// 参照した行の本文を、その日の today.md の初回の点検で控えておく。台帳が後から閉じられたり
// 行が足されたりすると、行番号は別の未処理の行を指したまま開いていて、開閉だけでは気づけない
// （2026-10-17、bbj の行番号が2つずれていたのに通った）。控えと本文が違えば、控えの本文を台帳から探し直す。
const SNAP = INST.file('data', 'today-refs.json');
const day = (lines[0] || '').trim();
let snap = {};
try { snap = JSON.parse(readFileSync(SNAP, 'utf8')); } catch {}
if (snap.day !== day) snap = { day, rows: {} };
const FIX = process.argv.includes('--fix');
const fixes = [];
for (const it of items) {
  for (const { path, n, kind } of refsOf(it.text)) {
    const file = resolve(path);
    if (!file) { flag(it, `\`${path}\` が見つからない`); continue; }
    const rows = readFileSync(file, 'utf8').split('\n');
    const row = rows[n - 1];
    const key = `${path}:${n}`;
    const pinned = snap.rows[key];
    if (pinned !== undefined && row !== pinned) {
      const moved = rows.findIndex((r) => r === pinned);
      if (moved === -1) flag(it, `\`${path}\` ${n}行に控えていた行が台帳から無くなった（閉じて退避されたか消えた）: ${pinned.trim().slice(0, 40)}`);
      else fixes.push({ it, path, kind, from: n, to: moved + 1 });
      continue;
    }
    if (row === undefined) flag(it, `\`${path}\` ${n}行が無い（行がずれたか消えた）`);
    else if (/^\s*- \[x\]/i.test(row)) flag(it, `\`${path}\` ${n}行は閉じている`);
    else if (!/^\s*- \[[ !]\]/.test(row)) flag(it, `\`${path}\` ${n}行は未処理の行ではない: ${row.trim().slice(0, 50)}`);
    else if (pinned === undefined) snap.rows[key] = row;
  }
}
// ずれた行番号。--fix なら、パスの後ろにある単独の「N行」（「同 N行」やパスを省いた番号を含む）を書き換え、
// 控えも新しい番号へ移す。列挙・範囲の中の番号（「8・9行」「64〜70行」）は別の番号を巻き込むので手で直す
for (const f of fixes) {
  const src = lines[f.it.line - 1];
  if (f.kind === 'link') {
    // pj リンクは URL の l= の中の番号を替える（l は署名の対象外）。範囲 a-b の中の番号は手で直す
    const re = new RegExp(`(\\(pj://open\\?[^)]*f=${f.path.split('/').pop().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}&l=(?:[^)&]*,)?)${f.from}(?=[,)&])`);
    if (FIX && re.test(src) && !new RegExp(`-${f.from}(?=[,)&])|${f.from}-`).test(src)) {
      lines[f.it.line - 1] = src.replace(re, `$1${f.to}`);
      snap.rows[`${f.path}:${f.to}`] = snap.rows[`${f.path}:${f.from}`];
      delete snap.rows[`${f.path}:${f.from}`];
      notes.push(`  ${f.it.line}行: \`${f.path}\` ${f.from}行を ${f.to}行へ直した（リンク）`);
    } else flag(f.it, `\`${f.path}\` ${f.from}行は ${f.to}行へずれた。${FIX ? 'リンクの範囲の中なので手で直す' : '--fix で直す'}`);
    continue;
  }
  const at = src.indexOf(`\`${f.path}\``);
  const re = new RegExp(`(?<![\\d〜・、,])${f.from}(?=\\s*行)(?!\\d)`);
  const tail = at === -1 ? '' : src.slice(at);
  const m = tail.match(re);
  const standalone = m && !/^\s*〜/.test(tail.slice(m.index + String(f.from).length));
  if (FIX && standalone) {
    lines[f.it.line - 1] = src.slice(0, at) + tail.slice(0, m.index) + f.to + tail.slice(m.index + String(f.from).length);
    snap.rows[`${f.path}:${f.to}`] = snap.rows[`${f.path}:${f.from}`];
    delete snap.rows[`${f.path}:${f.from}`];
    notes.push(`  ${f.it.line}行: \`${f.path}\` ${f.from}行を ${f.to}行へ直した`);
  } else flag(f.it, `\`${f.path}\` ${f.from}行は ${f.to}行へずれた。${standalone ? '--fix で直す' : '列挙・範囲の中なので手で直す'}`);
}
if (FIX && fixes.length) writeFileSync(target, lines.join('\n'));
writeFileSync(SNAP, JSON.stringify(snap, null, 1));

// 3. Gmail のスレッド
const ids = [...new Set(items.flatMap((it) => [...it.text.matchAll(/#all\/([0-9a-f]{16})/g)].map((m) => m[1])))];
if (ids.length && !GMAIL) notes.push('  Gmail の設定（gmail）が無いので、メールのスレッドを点検していない');
if (ids.length && GMAIL) {
  let token;
  try { token = await accessToken(); } catch (e) { problems.push(`  Gmail の認証に失敗したためスレッドを点検していない: ${e.message}`); }
  const get = async (path) => {
    const r = await fetch(`https://gmail.googleapis.com/gmail/v1/users/me/${path}`, { headers: { Authorization: `Bearer ${token}` } });
    return r.ok ? r.json() : null;
  };
  const fmt = (ms) => new Date(Number(ms)).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const myAddrs = token ? await myAddresses(get) : [];
  for (const id of token ? ids : []) {
    const msg = await get(`messages/${id}?format=minimal`);
    if (!msg) { problems.push(`  メール ${id} を開けない`); continue; }
    const th = await get(`threads/${msg.threadId}?format=metadata&metadataHeaders=Subject&metadataHeaders=From`);
    // 判定は lib/gmail-kind.mjs。下書きは送っていないので数えない（2026-10-18、返信の下書きを予約送信と読んだ）
    const later = (th?.messages || []).filter((m) => Number(m.internalDate) > Number(msg.internalDate) && kindOf(m, myAddrs) !== '下書き');
    const mine = (m) => kindOf(m, myAddrs) !== '届いた';
    const scheduled = (m) => kindOf(m, myAddrs) === '予約した';
    const hhmm = (ms) => new Date(Number(ms)).toLocaleTimeString('ja-JP', { timeZone: 'Asia/Tokyo', hour: '2-digit', minute: '2-digit' });
    const recv = later.filter((m) => !mine(m));
    for (const it of items.filter((x) => x.text.includes(id))) {
      const sent = later.filter((m) => mine(m) && !it.text.includes(hhmm(m.internalDate)));
      if (sent.length) flag(it, `リンク先の後に送信済み・予約送信がある（${sent.map((m) => `${fmt(m.internalDate)}${scheduled(m) ? '予約' : ''}「${header(m, 'Subject').slice(0, 30)}」`).join('、')}）。済んでいないか開いて確かめる`);
      const unread = recv.filter((m) => !it.text.includes(hhmm(m.internalDate)));
      const who = (m) => header(m, 'From').replace(/<.*>/, '').trim() || header(m, 'From').replace(/[<>]/g, '');
      if (unread.length) notes.push(`  ${it.line}行: リンク先の後に続報がある（${unread.map((m) => `${fmt(m.internalDate)} ${who(m)}`).join('、')}）`);
    }
  }
}

if (notes.length) console.log(`ℹ 続報・直した参照（中身を読んで行を直す）:\n${notes.join('\n')}`);
// 4. これから来る期限が today.md に載っているか。candidates.json は collect.mjs の出力
try {
  const cand = JSON.parse(readFileSync(INST.file('data', 'candidates.json'), 'utf8'));
  const seen = new Set(items.flatMap((x) => refsOf(x.text).map((r) => `${r.path}:${r.n}`)));
  for (const u of (cand.ledgers?.upcoming || []).filter((x) => x.deadline?.kind !== 'self')) {
    if (!seen.has(`${u.path}:${u.line}`)) problems.push(`  期限 ${u.deadline.ymd} の行が today.md に無い: ${u.path} ${u.line}行 ${u.text.replace(/\*\*/g, '').slice(0, 40)}`);
  }
  // 6. 返していない受信を引く台帳の行（ledgers.ballReturned）。同じ台帳の行に ID が複数あれば1回だけ言う
  const told = new Set();
  for (const b of cand.ledgers?.ballReturned || []) {
    const key = `${b.path}:${b.line}`;
    if (seen.has(key) || told.has(key)) continue;
    told.add(key);
    problems.push(`  返していない受信（${b.mail.from.replace(/<.*>/, '').replace(/"/g, '').trim()}「${b.mail.subject.slice(0, 24)}」）を引く行が today.md に無い: ${b.path} ${b.line}行 ${b.text.replace(/\*\*/g, '').slice(0, 40)}`);
  }
} catch (e) { notes.push(`candidates.json を読めず、期限の漏れを点検していない: ${e.message}`); }

// 6. 業務量（docs/design.md「業務量」）。「やること」の各項目の見積（項目名の行の「約30分」「約1時間30分」）の合計が、
//    載せる量の上限（candidates.json の capacity。2日分）に収まっているか。【期限超過】【最優先】は外せないので、
//    それだけで超えるなら注意に留める
// 7. 意識しておく案件（docs/design.md「意識しておく案件」）。節が「やること」の直下にあり、keep-in-mind.md の全案件が載っているか
try {
  const cand = JSON.parse(readFileSync(INST.file('data', 'candidates.json'), 'utf8'));
  const secs = [];
  let sec = '';
  lines.forEach((text) => { const h = text.match(/^## (.+)$/); if (h) { sec = h[1].trim(); secs.push(sec); } });
  const heads = items.filter((x) => x.section === 'やること');
  const minutesOf = (t) => { const m = stripLinks(t).match(/約\s*(?:(\d+)\s*時間)?\s*(?:(\d+)\s*分)?/); return m && (m[1] || m[2]) ? Number(m[1] || 0) * 60 + Number(m[2] || 0) : null; };
  // 見積を書く運用は features.workload の使い手だけ（使わない使い手の today.md は見積を持たない）
  const cap = INST.cfg.features.workload ? cand.capacity?.assignableMinutes : null;
  let sum = 0, forced = 0;
  for (const it of INST.cfg.features.workload ? heads : []) {
    const m = minutesOf(it.text);
    if (m == null) { flag(it, '見積が無い。項目名の行に「約30分」「約1時間30分」の形で所要時間を書く'); continue; }
    sum += m;
    if (/【期限超過】|【最優先】/.test(it.text)) forced += m;
  }
  if (cap != null && sum > cap) {
    const msg = `  「やること」の見積の合計 ${sum} 分が上限 ${cap} 分（${cand.capacity.days} 日分）を超えている`;
    if (forced >= cap) console.log(`ℹ${msg}（【期限超過】【最優先】だけで ${forced} 分。外す相談・期日の交渉を「やること」に入れる）`);
    else problems.push(`${msg}。優先度の低い項目を外す（外した項目は台帳に残る）`);
  }
  const keep = cand.keepInMind;
  if (keep?.found && keep.items.length) {
    const i = secs.indexOf('意識しておく案件');
    if (i === -1) problems.push('  「意識しておく案件」の節が無い（keep-in-mind.md の案件を「やること」の直下に優先順で出す）');
    else {
      if (secs[i - 1] !== 'やること') problems.push('  「意識しておく案件」の節を「やること」の直下に置く');
      const body = items.filter((x) => x.section === '意識しておく案件').map((x) => x.text).join('\n');
      for (const k of keep.items) if (!body.includes(k.title)) problems.push(`  意識しておく案件「${k.title}」が節に無い`);
    }
  }
} catch (e) { console.log(`ℹ candidates.json を読めず、業務量と意識しておく案件を点検していない: ${e.message}`); }
if (!problems.length) { console.log(`✅ ${items.length} 行を点検した。台帳の参照・メールのスレッド・出所に問題なし`); process.exit(0); }
console.log(`⚠ 済んでいないかを確かめる必要がある項目:\n${problems.join('\n')}`);
process.exit(1);
