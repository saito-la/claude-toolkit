/**
 * 決まった曜日の勤務先へ行けない・遅れる日を拾い、その勤務先の担当者へ連絡が済んでいるかを見る。正本。
 *
 * 2026-10-28 に作った。使い手が決まった曜日の午後に勤務先へ行けなかったのに、勤務先の事務の担当者が
 * 知らなかった。本務の側の用事が入った日は勤務先へ連絡が要るが、連絡したかを誰も見ていなかった。
 *
 * 設定はインスタンスの assistant.json の attendanceNotices（README.md「設定」）。運用は docs/design.md「勤務先への連絡」。
 *
 * 判定（規則ごと、今日から lookaheadDays 日）
 *   1. 曜日が weekdays に当たり、祝日でない日が対象。祝日は勤務先も休みなので見ない
 *   2. その日の予定のうち、departBy から to までに重なるものを拾う。departBy から from までは移動に充てる
 *      時間なので、ここに重なる予定は「遅れる」、from から to に重なる予定は「行けない」
 *   3. 拾わない予定：勤務場所の記載・自衛の枠（不在）・休み・欠席と書いた予定、オンラインの予定（勤務先から出られる）、
 *      勤務先の側の予定（conflict-rules.json の sides による side が skipSides に当たるもの）、
 *      題名が skipPattern に当たる予定（勤務先が主催する催しなど、連絡の要らないもの）。辞退した予定は呼び手が除く
 *   4. 連絡が済んだとみなすもの
 *      - 送信済み・予約送信のメールで、宛先（To・Cc）に notifyTo のどれかがいて、本文か件名にその日の日付
 *        （10/30・10月30日・2026-10-30 など）があるもの。その日のうちに送った通は、keywords の語があれば日付が無くてもよい
 *      - インスタンスの attendance-notices.csv にその日の行があるもの。Chat や口頭で伝えた分を使い手の申告で残す
 *        （notice-done.mjs が書く）
 *
 * 予定の場所の判定は conflicts.mjs の eventFacts を使う（呼び手が渡す）。
 */
import { readFileSync, existsSync } from 'node:fs';

export const MARKS_FILE = 'attendance-notices.csv';
export const MARKS_HEAD = '日付,規則,手段,記録日,備考';
const WD_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const WD_JA = ['日', '月', '火', '水', '木', '金', '土'];

const addDays = (ymd, n) => { const d = new Date(`${ymd}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const dow = (ymd) => new Date(`${ymd}T00:00:00Z`).getUTCDay();
const md = (ymd) => `${Number(ymd.slice(5, 7))}/${Number(ymd.slice(8, 10))}`;
export const dayLabel = (ymd) => `${md(ymd)}（${WD_JA[dow(ymd)]}）`;

// 規則の既定値。label・weekdays・from・to・notifyTo・draftLine は既定値を置けない（README.md「設定」）
const RULE_DEFAULTS = { lookaheadDays: 14, departBy: null, skipSides: ['workplace'], skipPattern: null, keywords: [], notifyName: '勤務先の担当者' };
const RULE_REQUIRED = ['label', 'weekdays', 'from', 'to', 'notifyTo', 'draftLine'];

/** 設定の規則に既定値を足す。既定値を置けない項目が無ければ名前を出して止まる */
export function withRuleDefaults(rule, i) {
  const miss = RULE_REQUIRED.filter((k) => rule[k] === undefined || rule[k] === null || rule[k] === '');
  if (miss.length) throw new Error(`assistant.json の attendanceNotices[${i}] に ${miss.join('・')} が無い（README.md「設定」）`);
  const r = { ...RULE_DEFAULTS, ...rule };
  r.departBy ??= r.from;
  return r;
}

/** インスタンスの attendance-notices.csv を読む。[{ date, rule, via, recorded, note }] */
export function loadMarks(file) {
  if (!existsSync(file)) return [];
  const [, ...rows] = readFileSync(file, 'utf8').replace(/^﻿/, '').trim().split('\n');
  return rows.filter((r) => r.trim()).map((r) => {
    const [date, rule, via, recorded, ...note] = r.split(',');
    return { date: date.trim(), rule: (rule || '').trim(), via: (via || '').trim(), recorded: (recorded || '').trim(), note: note.join(',').trim() };
  });
}

/** 規則の対象日（今日から lookaheadDays 日のうち、weekdays に当たり祝日でない日）。祝日で外した日も返す */
export function targetDays(rule, today, holidays) {
  const days = [];
  const skipped = [];
  for (let i = 0; i < rule.lookaheadDays; i++) {
    const d = addDays(today, i);
    if (!rule.weekdays.includes(WD_EN[dow(d)])) continue;
    (holidays?.has(d) ? skipped : days).push(d);
  }
  return { days, holidays: skipped };
}

/**
 * その日の予定（eventFacts の結果）から、勤務先へ行けない・遅れる原因になるものを拾う。
 * 前の日から続く予定・次の日へまたがる予定（学会の宿泊を含む枠など）も、時間帯に重なれば拾う
 */
export function blockingEvents(rule, ymd, facts, absent) {
  const at = (hm) => Date.parse(`${ymd}T${hm}:00+09:00`);
  const abs = (iso) => Date.parse(`${iso}:00+09:00`);
  const s = at(rule.departBy);
  const from = at(rule.from);
  const t = at(rule.to);
  const skip = rule.skipPattern ? new RegExp(rule.skipPattern, 'i') : null;
  const out = [];
  for (const f of facts) {
    if (f.allDay || f.kind !== 'appointment') continue;
    if (f.online || rule.skipSides.includes(f.side) || absent.test(f.title) || skip?.test(f.title)) continue;
    const a = abs(f.start);
    const b = abs(f.end);
    if (!(a < t && s < b)) continue;
    // 別の日にまたがる端は日付ごと書く
    const hm = (iso) => (iso.slice(0, 10) === ymd ? iso.slice(11) : `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))} ${iso.slice(11)}`);
    out.push({ start: hm(f.start), end: hm(f.end), title: f.title, side: f.side, effect: from < b ? 'absent' : 'late' });
  }
  return out;
}

/** その日の連絡に当たるメールか。text は件名と本文 */
export function mentionsDay(ymd, text, sentYmd, keywords) {
  const x = text.normalize('NFKC');
  const y = Number(ymd.slice(0, 4)); const m = Number(ymd.slice(5, 7)); const d = Number(ymd.slice(8, 10));
  const res = [
    new RegExp(`(?<![\\d/])0?${m}\\s*[/月]\\s*0?${d}(?!\\d)`),
    new RegExp(`${y}\\s*[-/年]\\s*0?${m}\\s*[-/月]\\s*0?${d}(?!\\d)`),
  ];
  if (res.some((r) => r.test(x))) return true;
  return sentYmd === ymd && keywords.some((k) => x.includes(k.normalize('NFKC')));
}

/**
 * 規則1つを判定する。
 *   facts    … 先 lookaheadDays 日の予定（eventFacts の結果）
 *   mails    … notifyTo へ送った通 [{ id, subject, date, sentYmd, text, link }]。null なら送信済みを読めていない
 *   marks    … attendance-notices.csv の行
 * 返す各項目：{ date, label, effect（absent 行けない／late 遅れる）, overrunMinutes（late のとき、departBy を何分過ぎるか）,
 *              events, notified: null | { via, ... } }
 */
export function judgeRule(rule, { today, holidays, facts, absent, mails, marks }) {
  const { days, holidays: skipped } = targetDays(rule, today, holidays);
  const items = [];
  for (const date of days) {
    const events = blockingEvents(rule, date, facts, absent);
    if (!events.length) continue;
    const mark = marks.find((k) => k.date === date && (!k.rule || k.rule === rule.label));
    const mail = (mails || []).find((x) => x.sentYmd <= date && mentionsDay(date, x.text, x.sentYmd, rule.keywords));
    const notified = mark ? { via: mark.via || '申告', recorded: mark.recorded, note: mark.note }
      : mail ? { via: 'mail', id: mail.id, subject: mail.subject, date: mail.date, link: mail.link } : null;
    const effect = events.some((e) => e.effect === 'absent') ? 'absent' : 'late';
    // 遅れるだけの日は、出る時刻を何分過ぎるかを添える（数分なら予定を早く抜けて済むことがある）
    const lastEnd = Math.max(...events.map((e) => Date.parse(`${date}T${e.end}:00+09:00`)));
    const overrun = effect === 'late' ? Math.round((lastEnd - Date.parse(`${date}T${rule.departBy}:00+09:00`)) / 60000) : null;
    items.push({ date, label: dayLabel(date), effect, ...(overrun !== null ? { overrunMinutes: overrun } : {}), events, notified });
  }
  const pending = items.filter((x) => !x.notified);
  return {
    label: rule.label,
    notifyName: rule.notifyName,
    notifyTo: rule.notifyTo,
    from: today, days: rule.lookaheadDays,
    targetDays: days, holidays: skipped,
    mailChecked: mails !== null,
    items,
    // 連絡の文面の案。手段（メールかチャットか）は使い手が決めるので、本文の行だけを作る
    draft: pending.length ? { lines: pending.map((x) => `${x.label}${rule.draftLine}`) } : null,
  };
}
