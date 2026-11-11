// Nextonton (nextonton.inotai.net) adapter — the successor of tonton.
//
// No browser: the page is a thin client over a public JSON API (read from /js/event.js and
// /js/common.js on 2026-11-11), so we call the API directly.
//
//   GET  /api/events/<id>                       → { title, days:[{date, slots}], participants:[{id, name, comment, pwSalt, marks}] }
//   POST /api/events/<id>/participants          { name, comment, marks, pwSalt, pwKey } → 201 { id }
//   POST /api/events/<id>/participants/<pid>/verify  { pwKey }   (403 = wrong password)
//   PUT  /api/events/<id>/participants/<pid>    { name, comment, marks, pwKey }
//
// slots: 48 chars, 30-minute slots from 06:00 to the next day's 05:30; '1' = proposed.
// marks: one 48-char string per day; 's' ★希望 / 'o' ●可 / 'm' ▲未定 / 'x' ×不可 / '-' not proposed.
// We map the judge's ◯/△/✕ to o/m/x and never use ★.
// pwKey = base64url(PBKDF2-SHA256(password, 'nittei-awase:' + pwSalt, 300000 rounds, 256 bit)).
//
// Re-answering overwrites the existing answer under displayName (same name-based rule as tonton),
// which needs config.browser.tontonEditPassword. Without it, or if it does not match, we stop with an
// error instead of adding a duplicate row.
import { webcrypto as crypto } from 'node:crypto';
import { judge } from '../availability.mjs';
export const supportsTriangle = true;
export const needsBrowser = false;

const BASE = 'https://nextonton.inotai.net';
const SLOTS = 48;
const FIRST_SLOT_MIN = 6 * 60;
const MARK = { '◯': 'o', '△': 'm', '✕': 'x' };

const pad = (n) => String(n).padStart(2, '0');
const hhmm = (min) => `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
const eventIdOf = (url) => {
  const m = url.match(/nextonton\.inotai\.net\/e\/([A-Za-z0-9_-]+)/i);
  if (!m) throw new Error(`Nextonton: URL からイベントIDを読めない (${url})`);
  return m[1];
};
const reviewUrlOf = (id) => `${BASE}/e/${id}`;
const addDays = (date, n) => {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

// Same error handling as the page's api(): Cloudflare's non-JSON errors (1027 = daily free quota
// used up, back at 09:00 JST; 429/1015 = rate limit) and the server's { overLimit } / { error }.
async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const fail = (msg) => Object.assign(new Error(`Nextonton: ${msg}`), { status: res.status });
  if (!res.ok && !(res.headers.get('content-type') || '').includes('json')) {
    const text = await res.text().catch(() => '');
    if (text.includes('1027')) throw fail('無料枠を使い切った（1027）。毎朝9時に戻るので次回に再試行する');
    if (res.status === 429 || text.includes('1015')) throw fail(`回数制限（${res.status}）。次回に再試行する`);
    throw fail(`HTTP ${res.status}`);
  }
  let data = {};
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok && data.overLimit) throw fail('無料枠を使い切った（overLimit）。毎朝9時に戻るので次回に再試行する');
  if (!res.ok) throw fail(data.error || `HTTP ${res.status}`);
  return data;
}

const getEvent = (id) => api(`/api/events/${encodeURIComponent(id)}?fresh=1`);

const b64url = (bytes) => Buffer.from(bytes).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const newSalt = () => b64url(crypto.getRandomValues(new Uint8Array(16)));
async function stretchPassword(password, salt) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: new TextEncoder().encode('nittei-awase:' + salt), iterations: 300000 },
    key, 256);
  return b64url(new Uint8Array(bits));
}

// Slot i of a day row as a judge() candidate. Slots from 24:00 on belong to the next calendar day.
function slotCandidate(date, i) {
  const s = FIRST_SLOT_MIN + i * 30;
  const day = Math.floor(s / 1440);
  const start = s % 1440;
  return { date: addDays(date, day), start: hhmm(start), end: hhmm(start + 30) };
}

export async function scrape(_page, url) {
  let ev;
  try {
    ev = await getEvent(eventIdOf(url));
  } catch (e) {
    if (e.status === 404) return { title: '', candidates: [] }; // deleted / expired → soft-skip upstream
    throw e;
  }
  const candidates = [...ev.days]
    .filter((d) => (d.slots || '').includes('1'))
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d, i) => ({ date: d.date, start: null, end: null, label: d.date, index: i }));
  return { title: ev.title, candidates };
}

// Runs of a mark in a row as "10:00-12:00, 15:00-17:00" (for the log).
function ranges(row, mark) {
  const out = [];
  for (let i = 0; i < row.length; i++) {
    if (row[i] !== mark) continue;
    const s = i;
    while (row[i + 1] === mark) i++;
    out.push(`${hhmm((FIRST_SLOT_MIN + s * 30) % 1440)}-${hhmm((FIRST_SLOT_MIN + (i + 1) * 30) % 1440)}`);
  }
  return out.join(', ');
}

export async function answer(_page, url, { displayName, decisions, dryRun, editPassword, busy, cfg }) {
  const id = eventIdOf(url);
  const ev = await getEvent(id);

  // marks must follow ev.days' own order (one string per day, same index as days[]).
  const marks = ev.days.map((d) => {
    let row = '';
    for (let i = 0; i < SLOTS; i++) {
      if (d.slots[i] !== '1') { row += '-'; continue; }
      row += MARK[judge(slotCandidate(d.date, i), busy, cfg, { supportsTriangle: true }).mark];
    }
    return row;
  });

  // Day-level marks for the log, derived from the exact slots submitted.
  const dayDecision = Object.fromEntries(decisions.map((d) => [d.candidate.date, d]));
  const dayMarks = {};
  ev.days.forEach((d, k) => {
    const row = marks[k];
    const n = (c) => row.split(c).length - 1;
    const on = n('o'), maybe = n('m'), total = on + maybe + n('x');
    if (!total) return;
    const why = dayDecision[d.date]?.reason;
    if (!on && !maybe) dayMarks[d.date] = { mark: '✕', reason: ['週末・休日', '祝日'].includes(why) ? why : '空き枠なし' };
    else {
      const parts = [`${on}/${total}枠が空き（${ranges(row, 'o') || 'なし'}）`];
      if (maybe) parts.push(`▲${maybe}枠（${ranges(row, 'm')}）`);
      dayMarks[d.date] = { mark: on === total ? '◯' : '△', reason: parts.join('・') };
    }
  });
  const totalOn = marks.join('').split('o').length - 1;
  const reviewUrl = reviewUrlOf(id);

  const mine = ev.participants.filter((p) => p.name === displayName);
  if (mine.length > 1) throw new Error(`Nextonton: 「${displayName}」の回答が${mine.length}件ある。どれを上書きするか決められないので手で整理する`);
  const prev = mine[0];
  const noPassword = !editPassword || editPassword.length < 4
    ? 'config.browser.tontonEditPassword（4文字以上）が未設定。回答の登録・上書きに要る' : '';

  if (dryRun) {
    return { submitted: false, reviewUrl, dayMarks,
      note: `dry-run: 空き${totalOn}枠を●（未送信${prev ? '・既存の回答を上書き予定' : ''}${noPassword ? `・送信不可：${noPassword}` : ''}）` };
  }
  if (noPassword) throw new Error(`Nextonton: ${noPassword}`);

  let pid;
  if (prev) {
    const pwKey = await stretchPassword(editPassword, prev.pwSalt);
    try {
      await api(`/api/events/${id}/participants/${prev.id}/verify`, { method: 'POST', body: { pwKey } });
    } catch (e) {
      if (e.status === 403) throw new Error(`Nextonton: 既存の「${displayName}」の回答の編集パスワードが config と合わない。手で直す`);
      throw e;
    }
    await api(`/api/events/${id}/participants/${prev.id}`, {
      method: 'PUT', body: { name: displayName, comment: prev.comment || '', marks, pwKey },
    });
    pid = prev.id;
  } else {
    const pwSalt = newSalt();
    const pwKey = await stretchPassword(editPassword, pwSalt);
    const res = await api(`/api/events/${id}/participants`, {
      method: 'POST', body: { name: displayName, comment: '', marks, pwSalt, pwKey },
    });
    pid = res.id;
  }

  // Confirm from the server that the saved row is exactly what we sent.
  const saved = (await getEvent(id)).participants.find((p) => p.id === pid);
  if (!saved || saved.name !== displayName || saved.marks.join() !== marks.join()) {
    throw new Error('Nextonton: 送信後にサーバー側の回答が送った内容と一致しない');
  }

  return { submitted: true, reviewUrl, dayMarks,
    note: `送信完了（空き${totalOn}枠●・他の候補枠は▲か×${prev ? '・既存の回答を上書き' : ''}）` };
}
