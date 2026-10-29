// よていくん (yotei-kun.jp) adapter.
//
// The page is a single-page app on Firebase. /s/<id> lands on /#event/<id>/answer, and the app
// exposes its state as window.S (event meta + answers) and its handlers on window
// (startAnswering / startEditingAnswer / submitAnswer). So we read S instead of scraping text.
//
// An answer is just a set of grid keys "<dayIndex>-<slotIndex>": selected = available, there is
// no △ or ✕. Days = startDate..endDate (Sat/Sun dropped when excludeWeekends); slots = tStart..tEnd
// in `step` minutes when mode==='time', else a single '終日' slot. We select the slots judged free
// inside business hours and leave every other slot empty.
//
// Answers are keyed by a uid the app keeps in localStorage (yotei_uid): the same uid overwrites,
// a new uid adds a row. Our browser is fresh every run, so to overwrite instead of duplicating we
// reuse the uid of the existing answer under displayName (the same name-based rule as tonton).
import { judge } from '../availability.mjs';
export const supportsTriangle = false;

const pad = (n) => String(n).padStart(2, '0');
const toMin = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };
const hhmm = (min) => `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;

// Same rule as the app's getEventDates (start..end inclusive, optionally weekdays only).
function eventDates(start, end, excludeWeekends) {
  const out = [];
  for (let d = new Date(`${start}T00:00:00Z`); d <= new Date(`${end}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    if (excludeWeekends && (d.getUTCDay() === 0 || d.getUTCDay() === 6)) continue;
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

// Slot start times in minutes (the app's getSlots); null = the single all-day slot.
function slotStarts(ev) {
  if (ev.mode !== 'time') return [null];
  const out = [];
  for (let m = toMin(ev.tStart); m < toMin(ev.tEnd); m += ev.step) out.push(m);
  return out;
}

async function load(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => window.S?.id && window.S.startDate, null, { timeout: 30000 });
  return readState(page);
}

function readState(page) {
  return page.evaluate(() => {
    const S = window.S;
    return {
      id: S.id, name: S.name, mode: S.mode, startDate: S.startDate, endDate: S.endDate,
      tStart: S.tStart, tEnd: S.tEnd, step: S.step || 60, excludeWeekends: !!S.excludeWeekends,
      is1on1: !!S.is1on1, closed: !!S.closed,
      answers: S.answers.map((a) => ({ name: a.name, uid: a.uid, sel: [...a.sel] })),
    };
  });
}

const resultUrl = (id) => `https://yotei-kun.jp/#event/${id}/result`;

export async function scrape(page, url) {
  const ev = await load(page, url);
  if (ev.is1on1) throw new Error('よていくん: 1on1（枠の予約）形式は未対応。手で回答する');
  // Closed poll = nothing to answer → soft-skip upstream (empty).
  if (ev.closed) return { title: ev.name, candidates: [] };
  const candidates = eventDates(ev.startDate, ev.endDate, ev.excludeWeekends)
    .map((date, i) => ({ date, start: null, end: null, label: date, index: i }));
  return { title: ev.name, candidates };
}

// Free slots of a day as "10:00-12:00, 15:00-17:00".
function ranges(starts, step) {
  const out = [];
  for (const m of starts) {
    const last = out[out.length - 1];
    if (last && last.e === m) last.e = m + step;
    else out.push({ s: m, e: m + step });
  }
  return out.map((r) => `${hhmm(r.s)}-${hhmm(r.e)}`).join(', ');
}

export async function answer(page, url, { displayName, decisions, dryRun, busy, cfg }) {
  let ev = await readState(page); // scrape() left the poll loaded
  const dates = decisions.map((d) => d.candidate.date);
  const starts = slotStarts(ev);

  // Judge every grid cell; select the free in-hours ones.
  const sel = [];
  const perDay = {};
  dates.forEach((date, di) => {
    perDay[date] = { on: [], off: 0 };
    starts.forEach((m, ti) => {
      const cand = m === null ? { date, start: null, end: null } : { date, start: hhmm(m), end: hhmm(m + ev.step) };
      const { mark } = judge(cand, busy, cfg, { supportsTriangle: false });
      if (mark === '◯') { sel.push(`${di}-${ti}`); perDay[date].on.push(m); } else perDay[date].off++;
    });
  });

  // Day-level marks for the log, derived from the exact cells submitted.
  const dayDecision = Object.fromEntries(decisions.map((d) => [d.candidate.date, d]));
  const dayMarks = {};
  for (const [date, pd] of Object.entries(perDay)) {
    const total = pd.on.length + pd.off;
    const why = dayDecision[date]?.reason;
    if (!pd.on.length) dayMarks[date] = { mark: '✕', reason: ['週末・休日', '祝日'].includes(why) ? why : '空き枠なし' };
    else if (pd.on[0] === null) dayMarks[date] = { mark: '◯', reason: '空き枠あり' };
    else dayMarks[date] = { mark: pd.off ? '△' : '◯', reason: `${pd.on.length}/${total}枠が空き（${ranges(pd.on, ev.step)}）` };
  }

  // Reuse our previous uid so a re-answer overwrites our row instead of adding one.
  const mine = ev.answers.filter((a) => a.name === displayName).pop();
  if (mine) {
    await page.evaluate((uid) => localStorage.setItem('yotei_uid', uid), mine.uid);
    ev = await load(page, url);
    await page.evaluate(() => window.startEditingAnswer());
  } else {
    await page.fill('#answer-name', displayName);
    await page.evaluate(() => window.startAnswering());
  }
  await page.waitForSelector('#grid-step', { state: 'visible', timeout: 8000 });

  // The grid's columns must be the dates we judged, or the keys would point at other days.
  const cols = await page.$$eval('#answer-grid .grid-col-label', (els) => els.map((e) => e.innerText.split('\n')[0]));
  const expected = dates.map((d) => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`);
  if (cols.join() !== expected.join()) throw new Error(`よていくん: 表の日付が想定と違う（表 ${cols.join(' ')} / 想定 ${expected.join(' ')}）`);

  await page.evaluate((keys) => {
    window.S.cur.sel = new Set(keys);
    for (const c of document.querySelectorAll('#answer-grid [data-k]')) {
      c.className = 'grid-cell ' + (window.S.cur.sel.has(c.dataset.k) ? 'cell-ok' : 'cell-empty');
    }
  }, sel);

  const reviewUrl = resultUrl(ev.id);
  if (dryRun) {
    return { submitted: false, reviewUrl, dayMarks, note: `dry-run: 空き${sel.length}枠を選択（未送信${mine ? '・既存の回答を上書き予定' : ''}）` };
  }

  await page.click('#submit-btn');
  await page.waitForFunction((id) => location.hash === `#event/${id}/result`, ev.id, { timeout: 20000 });
  const uid = await page.evaluate(() => localStorage.getItem('yotei_uid'));

  // The app navigates to the result even when the save only reached local storage, so confirm
  // from the server in a fresh browser context.
  const ctx = await page.context().browser().newContext({ locale: 'ja-JP', timezoneId: 'Asia/Tokyo' });
  try {
    const saved = (await load(await ctx.newPage(), url)).answers.find((a) => a.uid === uid);
    if (!saved || saved.name !== displayName || [...saved.sel].sort().join() !== [...sel].sort().join()) {
      throw new Error('よていくん: 送信後にサーバー側の回答が送った内容と一致しない');
    }
  } finally {
    await ctx.close();
  }

  return { submitted: true, reviewUrl, dayMarks,
    note: `送信完了（空き${sel.length}枠を選択${mine ? '・既存の回答を上書き' : ''}）` };
}
