/**
 * 作業用の文書の「# 2w Schedule」配下の日付見出し（見出し1）の書式。作るのも読むのもここを通す。
 *
 * 書式は「2027/1/30 Sat」（2026-10-24 に「2027.1.30 Sat」から改めた）。
 * 読む側は旧書式の「2026.7.28 Tue」と、曜日の無い「2027/1/30」も読む。旧書式の見出しは doc-2w.mjs が
 * 新しい書式へ書き換えるが、書き換えるまでの間と、記録用の文書へ移した後の見出しは旧書式のまま残るため。
 */
const WDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** "2027-01-30" → "2027/1/30 Sat" */
export function formatDateHeading(ymd) {
  const [y, m, d] = ymd.split('-').map(Number);
  return `${y}/${m}/${d} ${WDAY[new Date(`${ymd}T00:00:00Z`).getUTCDay()]}`;
}

/** 見出しの文字列にある日付を "YYYY-MM-DD" で返す。無ければ null（切り出す前の collect.mjs・doc-2w.mjs と同じく位置は問わない） */
export function ymdOfDateHeading(text) {
  const m = text.match(/(\d{4})[./](\d{1,2})[./](\d{1,2})/);
  return m ? `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}` : null;
}
