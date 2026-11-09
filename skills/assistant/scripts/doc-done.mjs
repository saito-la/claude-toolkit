#!/usr/bin/env node
/**
 * 作業用の文書（設定の workDoc）の当日見出し配下に「Done@…」ブロックを作る・差し替える。
 * 設定の features.done が true のときだけ動く。仕様の正本は ../docs/design.md「やったことの記録」。
 *
 *   node <エンジン>/scripts/doc-done.mjs [--heading "2026/9/20 Sun"] [--after "着席順資料"] [--apply] < lines.txt
 *
 * 行は標準入力から1行1項目で渡す（非ASCIIを引数に載せると壊れる端末があるため）。
 * 1行目は `Done@` で始まる見出し行とし、実際に使った機を毎回書く（固定文字列にしない）。
 * 既にブロックがあればその位置のまま差し替える——使い手がブロックを動かすため、
 * 見出しからの位置では捕捉できない。
 */
import { readFileSync } from 'fs';
import { requireFeature, need } from './lib/instance.mjs';
import { accessToken as tokenOf, getDoc, batchUpdate } from './lib/google.mjs';
import { formatDateHeading, ymdOfDateHeading } from './lib/date-heading.mjs';

requireFeature('done', 'やったことの記録');
const DOC_ID = need('workDoc.id');
const CRED = need('workDoc.credential');
const MARKER_PREFIX = 'Done@';

const argv = process.argv.slice(2);
const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv[i + 1]; };
const APPLY = argv.includes('--apply');
const AFTER = opt('--after');

// 日付見出しの書式は lib/date-heading.mjs。見出しは文字列ではなく日付で探す（旧書式の見出しが残っていても見つける）
function defaultHeading(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return formatDateHeading(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`);
}
const HEADING = opt('--heading') || defaultHeading();
const HEADING_YMD = ymdOfDateHeading(HEADING);
if (!HEADING_YMD) { console.error(`--heading に日付が無い: ${HEADING}`); process.exit(2); }

// 続きの行の頭の全角空白（同じプロジェクトの2つ目以降の作業の印）は残す。trim() は全角空白も消すので使わない
// 和文と英数字の間の半角空白は入れない（2026-10-17 決定）。英単語どうしの間の空白は残す
const JA = '[\\u3001-\\u30ff\\u3400-\\u9fff\\uff01-\\uffef]';
const tight = (s) => s
  .replace(new RegExp(`(${JA}) +(?=[!-~])`, 'g'), '$1')
  .replace(new RegExp(`([!-~]) +(?=${JA})`, 'g'), '$1');
const LINES = readFileSync(0, 'utf8').split('\n').map((s) => tight(s.replace(/^[ \t]+/, '').trimEnd())).filter((s) => s.trim());
if (!LINES.length) { console.error('標準入力が空です。1行1項目で渡してください'); process.exit(1); }
if (!LINES[0].startsWith(MARKER_PREFIX)) {
  console.error(`1行目は「${MARKER_PREFIX}」で始まる見出し行にしてください（例: ${MARKER_PREFIX}macbook・iphone w/claude:）。その日に実際に使った機を書く`);
  process.exit(1);
}

const token = await tokenOf(CRED);
const doc = await getDoc(DOC_ID, token);
const body = doc.tabs ? doc.tabs[0].documentTab.body : doc.body;

const paras = [];
for (const el of body.content || []) {
  if (!el.paragraph) continue;
  const text = (el.paragraph.elements || []).map((e) => (e.textRun ? e.textRun.content : '')).join('').replace(/\n$/, '');
  paras.push({
    start: el.startIndex,
    end: el.endIndex,
    h1: ((el.paragraph.paragraphStyle || {}).namedStyleType || '') === 'HEADING_1',
    text: text.trim(),
  });
}

const hi = paras.findIndex((p) => p.h1 && ymdOfDateHeading(p.text) === HEADING_YMD);
if (hi === -1) { console.error(`見出し1「${HEADING}」が見つかりません`); process.exit(1); }
let zoneEnd = hi + 1;
while (zoneEnd < paras.length && !paras[zoneEnd].h1) zoneEnd++;

const text = LINES.join('\n') + '\n';
const mi = paras.findIndex((p, i) => i > hi && i < zoneEnd && p.text.startsWith(MARKER_PREFIX));
const reqs = [];
let mode;

if (mi !== -1) {
  // 目印行から、次の空行の手前までを既存ブロックとみなして差し替える
  let be = mi;
  while (be + 1 < zoneEnd && paras[be + 1].text !== '') be++;
  console.log(`--- 差し替える既存ブロック（${be - mi + 1} 行）---`);
  for (let i = mi; i <= be; i++) console.log(`  ${paras[i].text}`);
  mode = `差し替え idx=${paras[mi].start}-${paras[be].end}`;
  reqs.push({ deleteContentRange: { range: { startIndex: paras[mi].start, endIndex: paras[be].end } } });
  reqs.push({ insertText: { location: { index: paras[mi].start }, text } });
} else {
  // 新規。--after があればその行の直後、無ければ見出し直下の最初のブロックの末尾。
  // いずれも空行を挟まない（2026-09-20 決定）。
  let ai = -1;
  if (AFTER) {
    ai = paras.findIndex((p, i) => i > hi && i < zoneEnd && p.text === AFTER);
    if (ai === -1) { console.error(`「${AFTER}」が「${HEADING}」配下に見つかりません`); process.exit(1); }
  } else {
    let i = hi + 1;
    while (i < zoneEnd && paras[i].text === '') i++;
    while (i < zoneEnd && paras[i].text !== '') { ai = i; i++; }
    if (ai === -1) ai = hi;
  }
  console.log(`--- 直後に入れる行: ${JSON.stringify(paras[ai].text)} ---`);
  mode = `新規挿入 idx=${paras[ai].end}`;
  reqs.push({ insertText: { location: { index: paras[ai].end }, text } });
}

// 挿入した段落は、挿入位置の段落の書式を引き継ぐ。直後が次の日付見出しだと見出し1になり、
// 次回はゾーンの終端と見なされて目印行が見つからなくなる（2026-10-17 に発生）。本文に戻す。
// 終端を1字手前にして、後ろの見出しの段落には掛けない。
const at = reqs[reqs.length - 1].insertText.location.index;
reqs.push({
  updateParagraphStyle: {
    range: { startIndex: at, endIndex: at + text.length - 1 },
    paragraphStyle: { namedStyleType: 'NORMAL_TEXT' },
    fields: 'namedStyleType',
  },
});

console.log(`--- ${mode} ---`);
console.log(text);
if (!APPLY) { console.log('dry-run。--apply で実行します。'); process.exit(0); }

await batchUpdate(DOC_ID, token, reqs);
console.log(`✅ ${mi !== -1 ? '差し替えました' : '挿入しました'}`);
