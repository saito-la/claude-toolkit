#!/usr/bin/env node
/**
 * 作業用の文書（設定の workDoc）の前日以前の日付見出しの欄を、記録用の文書（設定の logDoc）の
 * 先頭へ書式なしで写し、作業用の文書から消す。
 *
 *   node <エンジン>/scripts/doc-log.mjs [--apply]   # インスタンスのフォルダで実行する
 *
 * 移すのは「# 2w Schedule」配下の、今日より前の日付見出しごとに、見出しと直下の最初のかたまり
 * （空行までの連続した行。その日の予定と、doc-done.mjs がその直後に足す Done@ ブロック）。
 * かたまりの後ろにある日付の無いメモは移さない。見出しが消えると、そのメモは上の新しい日の欄に入る。
 * 使い手が手で移していた形（2026-10-16 分まで）に合わせた。
 *
 * Log は新しい日付が先頭。古い日から順に先頭へ入れ、最後に入れた日が一番上に来る。
 * 写すときは見出し・太字・斜体・下線・色・リンクを外し、本文の書式にする（2026-10-18 決定）。
 * 写した後に記録用の文書を読み戻して一致を確かめ、作業用の文書を読み直して消す範囲の本文が変わっていないことを
 * 確かめてから消す。どちらかが合わなければ作業用の文書は消さずに止まる。承認は取らない（2026-10-18 決定）。
 *
 * Done@ の行がかたまりの外にある日（使い手がブロックを動かした日）は、どこまで移すか決められないので止まる。
 * 規則の正本は ../docs/daily-and-update.md「5. 作業用の文書の前日分を記録用の文書へ移す」。
 */
import { instance, need } from './lib/instance.mjs';
import { accessToken, getDoc, batchUpdate as update, paragraphs as paras } from './lib/google.mjs';
import { ymdOfDateHeading } from './lib/date-heading.mjs';

const CFG = instance().cfg;
// 2つの文書は別のアカウントのことがある（勤務先のアカウントと個人のアカウントなど）。資格情報は文書ごとに持つ
const KYO = need('workDoc.id');
const LOG = need('logDoc.id');
const KYO_NAME = CFG.workDoc.name;
const LOG_NAME = CFG.logDoc.name;
const APPLY = process.argv.includes('--apply');

const tokens = { [KYO]: await accessToken(need('workDoc.credential')), [LOG]: await accessToken(need('logDoc.credential')) };

async function paragraphs(docId) {
  return paras(await getDoc(docId, tokens[docId])).map(({ start, end, h1, text }) => ({ start, end, h1, text }));
}
const batchUpdate = (docId, requests) => update(docId, tokens[docId], requests);

const now = new Date();
const today = now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
const ymdOf = (text) => {
  const ymd = ymdOfDateHeading(text);
  return ymd ? Number(ymd.replaceAll('-', '')) : null;
};

// 移す範囲を決める。返すのは作業用の文書の段落の番号の範囲 [from, to]（to は末尾の空行を含む）と、写す行
function plan(kyo) {
  const top = kyo.findIndex((p) => p.h1 && p.text.trim() === '# 2w Schedule');
  const sink = kyo.findIndex((p) => p.h1 && p.text.trim() === '# Sink');
  if (top === -1 || sink === -1) throw new Error('「# 2w Schedule」か「# Sink」が見つからない');
  const days = [];
  for (let i = top + 1; i < sink; i++) {
    const ymd = kyo[i].h1 ? ymdOf(kyo[i].text) : null;
    if (!ymd || ymd >= today) continue;
    let zoneEnd = i + 1;
    while (zoneEnd < sink && !kyo[zoneEnd].h1) zoneEnd++;
    let j = i + 1;
    while (j < zoneEnd && kyo[j].text.trim() === '') j++;
    while (j < zoneEnd && kyo[j].text.trim() !== '') j++;
    const blockEnd = j - 1;                    // 最初のかたまりの最後の行
    const to = j < zoneEnd ? j : blockEnd;      // 後ろの空行1つも一緒に消す
    const lines = kyo.slice(i, blockEnd + 1).map((p) => p.text).filter((t, k) => k === 0 || t.trim() !== '');
    const strayDone = kyo.slice(blockEnd + 1, zoneEnd).find((p) => p.text.startsWith('Done@'));
    days.push({ ymd, heading: kyo[i].text.trim(), from: i, to, lines, strayDone: !!strayDone });
  }
  return days.sort((a, b) => a.ymd - b.ymd);
}

let kyo = await paragraphs(KYO);
const days = plan(kyo);
if (!days.length) { console.log(`${KYO_NAME} に前日以前の日付見出しは無い。移すものは無かった`); process.exit(0); }
for (const d of days) {
  console.log(`--- ${d.heading}（${d.lines.length - 1} 行）---`);
  for (const t of d.lines.slice(1)) console.log(`  ${t}`);
  if (CFG.features.done && !d.lines.some((t) => t.startsWith('Done@'))) console.log('  （Done@ の行が無い。doc-done.mjs を先に流したか確かめる）');
}
const stray = days.filter((d) => d.strayDone);
if (stray.length) {
  console.error(`✖ Done@ の行が最初のかたまりの外にある日がある：${stray.map((d) => d.heading).join('、')}。どこまで移すか手で決める`);
  process.exit(1);
}
if (!APPLY) { console.log('dry-run。--apply で実行します。'); process.exit(0); }

// 1. 記録用の文書の先頭へ、古い日から順に入れる（最後に入れた日が一番上）。日の区切りは空行1つ
for (const d of days) {
  const text = d.lines.join('\n') + '\n\n';
  await batchUpdate(LOG, [
    { insertText: { location: { index: 1 }, text } },
    { updateParagraphStyle: { range: { startIndex: 1, endIndex: 1 + text.length - 1 }, paragraphStyle: { namedStyleType: 'NORMAL_TEXT' }, fields: 'namedStyleType' } },
    { updateTextStyle: { range: { startIndex: 1, endIndex: 1 + text.length - 1 }, textStyle: {}, fields: 'bold,italic,underline,strikethrough,foregroundColor,backgroundColor,link,fontSize,weightedFontFamily,baselineOffset,smallCaps' } },
  ]);
}

// 2. 記録用の文書を読み戻し、先頭が新しい日から順に写した行と一致するか確かめる
const want = [...days].reverse().flatMap((d) => [...d.lines, '']);
const got = (await paragraphs(LOG)).slice(0, want.length).map((p) => p.text);
const bad = want.findIndex((t, i) => t !== got[i]);
if (bad !== -1) {
  console.error(`✖ ${LOG_NAME} の読み戻しが合わない（${bad + 1} 行目：写した「${want[bad]}」／${LOG_NAME}「${got[bad]}」）。${KYO_NAME} は消していない`);
  process.exit(1);
}

// 3. 作業用の文書を読み直し、消す範囲の本文が変わっていなければ下から消す
kyo = await paragraphs(KYO);
const again = plan(kyo);
const same = again.length === days.length && again.every((d, k) => d.heading === days[k].heading && d.lines.join('\n') === days[k].lines.join('\n'));
if (!same) {
  console.error(`✖ 写している間に ${KYO_NAME} の前日分が書き換わった。${LOG_NAME} には写したが、${KYO_NAME} は消していない。${LOG_NAME} の先頭を見て直す`);
  process.exit(1);
}
const deletes = [...again].sort((a, b) => b.from - a.from)
  .map((d) => ({ deleteContentRange: { range: { startIndex: kyo[d.from].start, endIndex: kyo[d.to].end } } }));
await batchUpdate(KYO, deletes);
console.log(`✅ ${days.map((d) => d.heading).join('、')} を ${LOG_NAME} の先頭へ写し、読み戻しが一致したので ${KYO_NAME} から消した`);
