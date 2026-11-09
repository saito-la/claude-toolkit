#!/usr/bin/env node
/**
 * doc-remove.mjs — action-items.md へ移送し終えた行を作業用の文書（設定の workDoc）から削除する
 *
 * docs/design.md「書き込み方針」の「作業用の文書からの削除」。
 *
 * 照合は本文の完全一致で行う。作業用の文書は随時編集されるため段落インデックスは取得直後から
 * ずれる。candidates.json の docIndex は削除の宛先には使えない。
 *
 * 探索範囲は現役ゾーン（捕捉ゾーン＋直近ToDoゾーン）に限る。Backlog・Sink 帯には
 * 同一文言が眠っていることがあり、範囲を絞らないと沈殿側を消しうる。
 *
 * Usage:
 *   node doc-remove.mjs --lines-file <path>        # dry-run（消す行を出すだけ）。インスタンスのフォルダで実行する
 *   node doc-remove.mjs --lines-file <path> --apply
 *   node doc-remove.mjs --line "w 〇〇さんへ連絡" --line "..." --apply
 *
 *   --lines-file は1行1件の平文。空行と # 始まりの行は無視する。
 *
 * 中止する条件（いずれも黙って進めない）
 *   - 指定行が現役ゾーンに見つからない
 *   - 現役ゾーンに同一行が複数ある（どちらを消すか決められない）
 *   - 「# Sink」が1個でない
 */

import { readFileSync } from 'fs';
import { instance, need } from './lib/instance.mjs';
import { accessToken as tokenOf, getDoc, batchUpdate, paragraphs } from './lib/google.mjs';

const DOC_ID = need('workDoc.id');
const CRED = need('workDoc.credential');
const DOC = instance().cfg.workDoc.name;
const SCHEDULE_HEADING = '# 2w Schedule';
const CAPTURE_HEADING = '# Capture';
const BACKLOG_HEADING = '# Backlog';
const SINK_HEADING = '# Sink';

// ── 引数 ───────────────────────────────────────────────
const argv = process.argv.slice(2);
const APPLY = argv.includes('--apply');
const targets = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--line' && argv[i + 1]) targets.push(argv[++i].trim());
  if (argv[i] === '--lines-file' && argv[i + 1]) {
    for (const l of readFileSync(argv[++i], 'utf8').split('\n')) {
      const t = l.trim();
      if (t && !t.startsWith('#')) targets.push(t);
    }
  }
}
if (!targets.length) {
  console.error('削除対象がありません。--lines-file <path> か --line "本文" を指定してください。');
  process.exit(1);
}

// ── 作業用の文書 ────────────────────────────────────────
async function getParagraphs(token) {
  return paragraphs(await getDoc(DOC_ID, token)).map(({ start, end, h1, text }) => ({ start, end, h1, text: text.trim() }));
}

const findH1 = (paras, label) => paras.findIndex((p) => p.h1 && p.text === label);
const countH1 = (paras, label) => paras.filter((p) => p.h1 && p.text === label).length;
const mdKey = (t) => (t.match(/(\d{4})[./](\d{1,2})[./](\d{1,2})/) ? true : false);

/** 現役ゾーンの [from, to) 区間を返す（段落配列上のインデックス） */
function liveRanges(paras) {
  const ranges = [];

  const ci = findH1(paras, CAPTURE_HEADING);
  const bi = findH1(paras, BACKLOG_HEADING);
  if (ci !== -1) {
    let end = bi !== -1 && bi > ci ? bi : paras.findIndex((p, i) => i > ci && p.h1);
    if (end === -1) end = paras.length;
    ranges.push({ name: '捕捉', from: ci + 1, to: end });
  }

  const si2w = findH1(paras, SCHEDULE_HEADING);
  if (si2w === -1) throw new Error(`「${SCHEDULE_HEADING}」が見つかりません`);
  let lastDate = -1;
  for (let i = si2w + 1; i < paras.length; i++) {
    if (!paras[i].h1) continue;
    if (!mdKey(paras[i].text)) break;
    lastDate = i;
  }
  if (lastDate === -1) throw new Error(`「${SCHEDULE_HEADING}」配下に日付見出しがありません`);

  if (countH1(paras, SINK_HEADING) !== 1) {
    throw new Error(`「${SINK_HEADING}」が ${countH1(paras, SINK_HEADING)} 個あります（1個であるべき）。現役ゾーンを確定できません`);
  }
  const si = findH1(paras, SINK_HEADING);
  if (si <= lastDate) throw new Error(`「${SINK_HEADING}」が当日見出しより前にあります。${DOC} の構造を確認してください`);
  ranges.push({ name: '直近', from: lastDate + 1, to: si });

  return ranges;
}

// ── 照合 ───────────────────────────────────────────────
const token = await tokenOf(CRED);
const paras = await getParagraphs(token);
const ranges = liveRanges(paras);
console.log(`現役ゾーン：${ranges.map((r) => `${r.name} ${r.to - r.from} 段落`).join(' / ')}`);

const plan = [];
const problems = [];
for (const t of targets) {
  const hits = [];
  for (const r of ranges) {
    for (let i = r.from; i < r.to; i++) {
      if (paras[i].text && paras[i].text === t) hits.push({ zone: r.name, p: paras[i] });
    }
  }
  if (hits.length === 0) problems.push(`見つからない: ${t}`);
  else if (hits.length > 1) problems.push(`${hits.length} 箇所に同一行がある（曖昧）: ${t}`);
  else plan.push({ text: t, zone: hits[0].zone, start: hits[0].p.start, end: hits[0].p.end });
}

if (problems.length) {
  console.error('\n中止します。以下を解消してください。');
  problems.forEach((p) => console.error(`  ✗ ${p}`));
  process.exit(1);
}

console.log(`\n--- 削除する ${plan.length} 行 ---`);
plan.forEach((p) => console.log(`  [${p.zone}] ${p.text}`));

if (!APPLY) {
  console.log('\ndry-run。--apply で削除します。');
  process.exit(0);
}

// 後ろから消す（前を消すと後続のインデックスがずれる）
const reqs = plan
  .slice()
  .sort((a, b) => b.start - a.start)
  .map((p) => ({ deleteContentRange: { range: { startIndex: p.start, endIndex: p.end } } }));

await batchUpdate(DOC_ID, token, reqs);

// 消えたことを読み戻して確認する
const after = await getParagraphs(token);
const afterRanges = liveRanges(after);
const remaining = [];
for (const p of plan) {
  for (const r of afterRanges) {
    for (let i = r.from; i < r.to; i++) if (after[i].text === p.text) remaining.push(p.text);
  }
}
if (remaining.length) {
  console.error(`\n⚠️  削除後も現役ゾーンに残っています（${remaining.length} 件）`);
  remaining.forEach((t) => console.error(`  ${t}`));
  process.exit(1);
}
console.log(`\n✅ ${plan.length} 行を ${DOC} から削除し、読み戻して消えたことを確認しました`);
