/**
 * 祝日を Google カレンダー「日本の祝日」から引く。
 *
 * 設定の holidays.calendarId（既定 日本の祝日）を、holidays.credential（既定 calendars の最初の Google の予定表の
 * 資格情報）で読む。公開の予定表なので、calendar.readonly を持つ資格情報なら何でも読める。
 * この予定表は祝日のほかに節分・七夕・クリスマスなどの「祭日」も返す。説明欄が「祝日」のものだけを祝日とする
 * （2026-10-28 に実データで確かめた。祭日の説明欄は「祭日」と非表示の案内）。
 *
 * 読めなければ null を返す（祝日を除けないことを呼び手が警告に出す）。
 */
import { instance } from './instance.mjs';
import { accessToken } from './google.mjs';
import { isFileCalendar } from './calendars.mjs';

/** fromYmd から toYmd の前日までの祝日の Set（YYYY-MM-DD）。読めなければ null */
export async function fetchHolidays(fromYmd, toYmd, warn = () => {}) {
  const { cfg } = instance();
  const credential = cfg.holidays.credential || cfg.calendars.find((c) => !isFileCalendar(c))?.credential;
  if (!credential) { warn('祝日を読む資格情報が無い（holidays.credential か Google の予定表の設定が要る）'); return null; }
  let token;
  try {
    token = await accessToken(credential);
  } catch (e) {
    warn(`祝日の予定表の認証に失敗: ${String(e.message).slice(0, 120)}`);
    return null;
  }
  const q = new URLSearchParams({
    timeMin: `${fromYmd}T00:00:00+09:00`,
    timeMax: `${toYmd}T00:00:00+09:00`,
    singleEvents: 'true',
    maxResults: '250',
  });
  const res = await fetch(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(cfg.holidays.calendarId)}/events?${q}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();
  if (!res.ok) { warn(`祝日の予定表の取得に失敗: ${JSON.stringify(data).slice(0, 200)}`); return null; }
  return new Set((data.items || []).filter((e) => e.start?.date && (e.description || '').trim() === '祝日').map((e) => e.start.date));
}
