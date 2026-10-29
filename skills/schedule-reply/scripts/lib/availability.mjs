// Judge each candidate against the configured Google Calendar's busy intervals.
// Constraint: weekday (Mon–Fri) AND inside business hours (08:00–17:00 JST).
// A public holiday (cfg.availability.holidays, filled by poll.mjs) is ✕ 祝日 in either form.
//
// Explicit slot (start+end given, e.g. 調整さん "10:00〜11:00"):
//   outside hours / weekend        -> ✕
//   free                           -> ◯ 空き
//   partial overlap                -> △ 一部予定あり (✕ if tool has no △)
//   full overlap                   -> ✕ 予定重複
//
// Date-only / whole-day (tonton day column): is there a free block of at least
// the meeting length inside business hours?
//   weekend                        -> ✕ 週末・休日
//   no free block >= slot          -> ✕ 空き枠なし
//   free block exists, day ≥半分空き -> ◯ 空き枠あり
//   free block exists, day混雑      -> △ 一部空き(要調整)

const toMin = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };
const at = (dateStr, hhmm) => new Date(`${dateStr}T${hhmm}:00+09:00`);

function jstWeekday(dateStr) {
  const d = new Date(`${dateStr}T12:00:00+09:00`);
  const wd = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Tokyo', weekday: 'short' }).format(d);
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(wd);
}

function overlapMinutes(start, end, busy) {
  let overlap = 0;
  for (const b of busy) {
    const s = Math.max(start.getTime(), b.start.getTime());
    const e = Math.min(end.getTime(), b.end.getTime());
    if (e > s) overlap += (e - s) / 60000;
  }
  return overlap;
}

// Free intervals (minutes-from-midnight pairs) inside [winStart,winEnd] for a date.
function freeBlocks(dateStr, winStart, winEnd, busy) {
  const dayStart = at(dateStr, winStart).getTime();
  const dayEnd = at(dateStr, winEnd).getTime();
  const within = busy
    .map((b) => ({ s: Math.max(b.start.getTime(), dayStart), e: Math.min(b.end.getTime(), dayEnd) }))
    .filter((b) => b.e > b.s)
    .sort((a, b) => a.s - b.s);
  const blocks = [];
  let cursor = dayStart;
  for (const b of within) {
    if (b.s > cursor) blocks.push({ s: cursor, e: b.s });
    cursor = Math.max(cursor, b.e);
  }
  if (cursor < dayEnd) blocks.push({ s: cursor, e: dayEnd });
  return blocks.map((b) => (b.e - b.s) / 60000);
}

// candidate: { date:'YYYY-MM-DD', start:'HH:MM'|null, end:'HH:MM'|null }
export function judge(candidate, busy, cfg, { supportsTriangle = true } = {}) {
  const av = cfg.availability;
  if (!av.businessDays.includes(jstWeekday(candidate.date))) return { mark: '✕', reason: '週末・休日' };
  if (av.holidays?.includes(candidate.date)) return { mark: '✕', reason: '祝日' };

  const bStart = toMin(av.businessStart);
  const bEnd = toMin(av.businessEnd);

  // ---- explicit slot ----
  if (candidate.start) {
    let endStr = candidate.end;
    if (!endStr) {
      const e = toMin(candidate.start) + (av.slotMinutesWhenNoTime || 60);
      endStr = `${String(Math.floor(e / 60)).padStart(2, '0')}:${String(e % 60).padStart(2, '0')}`;
    }
    if (toMin(candidate.start) < bStart || toMin(endStr) > bEnd) return { mark: '✕', reason: '時間外' };
    const start = at(candidate.date, candidate.start);
    const end = at(candidate.date, endStr);
    const slotMin = (end - start) / 60000;
    const ov = overlapMinutes(start, end, busy);
    if (ov <= 0) return { mark: '◯', reason: '空き' };
    if (ov >= slotMin - 0.5) return { mark: '✕', reason: '予定重複' };
    return supportsTriangle ? { mark: '△', reason: '一部予定あり' } : { mark: '✕', reason: '一部予定あり(△非対応)' };
  }

  // ---- date-only / whole business-day ----
  const slot = av.slotMinutesWhenNoTime || 60;
  const windowMin = bEnd - bStart;
  const blocks = freeBlocks(candidate.date, av.businessStart, av.businessEnd, busy);
  const maxBlock = blocks.length ? Math.max(...blocks) : 0;
  const freeMin = blocks.reduce((a, b) => a + b, 0);
  if (maxBlock < slot) return { mark: '✕', reason: '空き枠なし' };
  if (freeMin >= windowMin * 0.5) return { mark: '◯', reason: '空き枠あり' };
  return supportsTriangle ? { mark: '△', reason: '一部空き(要調整)' } : { mark: '◯', reason: '空き枠あり' };
}

const ABSENCE_KEYWORDS_DEFAULT = ['出張', '休暇', '有給', '不在', '休み', '休業'];

// All-day events default to transparency=transparent in Google Calendar, so
// freeBusy silently drops them (e.g. a multi-day business-trip event created
// without explicitly setting transparency=opaque). Recover any all-day event
// whose title looks like an absence and fold it into the busy list — even
// though the calendar says "free", it should still count as busy here.
// Events already opaque are skipped (freeBusy already counted them); this is
// a fallback for the transparent case only, gated on a title keyword so an
// unrelated all-day event (anniversary, non-absence note) isn't force-busied.
export function mergeAbsenceEvents(busy, allDayEvents, keywords = ABSENCE_KEYWORDS_DEFAULT) {
  const extra = [];
  for (const ev of allDayEvents || []) {
    if (ev.transparency !== 'transparent') continue;
    if (!keywords.some((k) => ev.summary.includes(k))) continue;
    extra.push({
      start: new Date(`${ev.start}T00:00:00+09:00`),
      end: new Date(`${ev.end}T00:00:00+09:00`), // events.list end.date is exclusive (matches busy semantics)
    });
  }
  if (!extra.length) return busy;
  return [...busy, ...extra].sort((a, b) => a.start - b.start);
}

// Dates (YYYY-MM-DD) of the public holidays in a holiday calendar's all-day events. Google's
// holiday calendars also carry observances (節分・七夕・クリスマス…), marked by a description that
// starts with 祭日 / Observance; those are working days and are left out.
export function holidayDates(events) {
  const out = new Set();
  for (const ev of events || []) {
    if (/^(祭日|Observance)/.test(ev.description || '')) continue;
    for (let d = new Date(`${ev.start}T00:00:00Z`); d < new Date(`${ev.end}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
      out.add(d.toISOString().slice(0, 10));
    }
  }
  return [...out];
}

// Bounding window (ISO) covering all candidates, for a single freeBusy query.
export function boundingWindow(candidates) {
  const dates = candidates
    .map((c) => c.date)
    .filter((d) => !Number.isNaN(new Date(`${d}T00:00:00+09:00`).getTime()))
    .sort();
  if (!dates.length) throw new Error('boundingWindow: no valid candidate dates');
  return {
    timeMin: new Date(`${dates[0]}T00:00:00+09:00`).toISOString(),
    timeMax: new Date(`${dates[dates.length - 1]}T23:59:59+09:00`).toISOString(),
  };
}
