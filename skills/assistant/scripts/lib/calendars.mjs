/**
 * ファイルの予定表（設定の calendars[] のうち type が "file" のもの）を読み、Google Calendar API の events.list と
 * 同じ形の予定に直して返す。予定を使う側（collect.mjs・doc-2w.mjs・conflicts.mjs）は Google の形だけを扱えばよい。
 *
 * Google の API で読めない予定表を、別の道具が書き出したファイルで渡すためにある。最初の使い道は Outlook の
 * 予定表で、組織の端末でブラウザの Outlook（OWA）の画面から書き出したもの。
 * ファイルの形はその出力：
 *   { generated, from, to, events: [{ start, end, allDay, title, showAs, organizer, location, recurring }] }
 *   時刻つきは start・end が "YYYY-MM-DDTHH:MM"（日本時間）。終日は "YYYY-MM-DD" で、end はその日を含む
 *
 * 設定：{ "type": "file", "label": "Outlook", "path": "data/outlook-calendar.json", "maxAgeHours": 24 }
 *   path はインスタンスのフォルダからの相対か、~/ で始まる絶対。maxAgeHours（既定 24）より古ければ警告する
 *
 * ファイルが無い・読めないときは null を返し、理由を warnings に入れる。書き出す道具が動かない端末でも、同じ設定のまま
 * 他の予定表だけで回せるようにするため（doc-2w.mjs は足りない行を足すだけなので、飛ばしても他の予定を壊さない）。
 */
import { readFileSync, statSync } from 'fs';
import { isAbsolute } from 'path';
import { instance, expandHome } from './instance.mjs';

const addDays = (ymd, n) => { const d = new Date(`${ymd}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

export const isFileCalendar = (cal) => cal.type === 'file';

function toGoogle(ev, i) {
  const out = {
    id: `file-${i}`,
    status: 'confirmed',
    summary: ev.title,
    location: ev.location || undefined,
    // 主催者の表示が無い予定は本人が作ったもの（ブラウザの Outlook は本人が主催者の予定に名前を出さない）
    organizer: ev.organizer ? { displayName: ev.organizer } : { self: true },
    attendees: [
      { self: true, responseStatus: ev.showAs === 'Tentative' ? 'tentative' : 'accepted' },
      ...(ev.organizer ? [{ displayName: ev.organizer }] : []),
    ],
    transparency: ev.showAs === 'Free' ? 'transparent' : 'opaque',
    ...(ev.recurring ? { recurringEventId: `file-recurring-${i}` } : {}),
  };
  if (ev.allDay) {
    out.start = { date: ev.start };
    out.end = { date: addDays(ev.end, 1) }; // Google の終日の end は含まない
  } else {
    out.start = { dateTime: `${ev.start}:00+09:00` };
    out.end = { dateTime: `${ev.end}:00+09:00` };
  }
  return out;
}

/**
 * fromYmd から toYmd（含まない）にかかる予定を Google の形で返す。読めなければ null。
 * warnings には、読めない理由か、古い・範囲が足りないといった注意を入れる
 */
export function fileEvents(cal, fromYmd, toYmd, warnings) {
  const raw = expandHome(cal.path || '');
  const path = isAbsolute(raw) ? raw : instance().file(raw);
  let data;
  try {
    data = JSON.parse(readFileSync(path, 'utf8'));
  } catch (e) {
    warnings.push(`${cal.label} の予定表のファイルを読めない（${path}）: ${String(e.message).slice(0, 100)}`);
    return null;
  }
  const ageH = (Date.now() - (Date.parse(data.generated) || statSync(path).mtimeMs)) / 3_600_000;
  if (ageH > (cal.maxAgeHours ?? 24)) warnings.push(`${cal.label} の予定表のファイルは ${Math.round(ageH)} 時間前のもの。書き出し直していない`);
  if (data.from > fromYmd || data.to < toYmd) warnings.push(`${cal.label} の予定表のファイルは ${data.from}〜${addDays(data.to, -1)} の分しか無く、${fromYmd}〜${addDays(toYmd, -1)} を覆っていない`);
  return (data.events || [])
    .map(toGoogle)
    .filter((ev) => {
      const s = (ev.start.date || ev.start.dateTime).slice(0, 10);
      const e = ev.end.date ? addDays(ev.end.date, -1) : ev.end.dateTime.slice(0, 10);
      return e >= fromYmd && s < toYmd;
    })
    .sort((a, b) => (a.start.date || a.start.dateTime).localeCompare(b.start.date || b.start.dateTime));
}
