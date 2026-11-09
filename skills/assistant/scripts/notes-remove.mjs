#!/usr/bin/env node
/**
 * notes-remove.mjs — action-items.md 等へ移送し終えた行を Apple Notes の
 * 捕捉用のノート（設定の appleNotes.title、既定「Capture」）から削除する。doc-remove.mjs の Apple Notes 版。
 * 設定の features.appleNotes が true のときだけ動く。
 *
 * Capture ノートの body は 1行＝1つの <div>...</div> ブロックという構造を前提にする
 * （2026-08-01 実測。チェックリスト・表等が混ざると <ul>/<table> が現れるため、
 * それらを検出したら安全側に倒して中止する）。
 *
 * 照合は本文の完全一致（HTMLタグを除去したプレーンテキスト）で行う。
 *
 * Usage:
 *   node notes-remove.mjs --lines-file <path>        # dry-run（消す行を出すだけ）
 *   node notes-remove.mjs --lines-file <path> --apply
 *   node notes-remove.mjs --line "Kentaについて考察する。" --apply
 *
 *   --lines-file は1行1件の平文。空行と # 始まりの行は無視する。
 *
 * 中止する条件（いずれも黙って進めない）：
 *   - 「Capture」ノートが見つからない
 *   - 指定行が見つからない・複数箇所にある（曖昧）
 *   - body に <ul>・<ol>・<table> など <div> 以外の入れ子構造がある（想定外の破壊防止）
 *   - 削除後、読み戻して該当行が残っている
 */
import { readFileSync, writeFileSync, unlinkSync } from 'fs';
import { execFileSync } from 'child_process';
import { tmpdir } from 'os';
import { join } from 'path';
import { instance, requireFeature } from './lib/instance.mjs';

requireFeature('appleNotes', 'Apple Notes の取り込み');
const NOTES_TITLE = instance().cfg.appleNotes.title;

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

// ── Apple Notes ────────────────────────────────────────
function getBody() {
  const script = `
tell application "Notes"
  set matches to every note whose name is "${NOTES_TITLE}"
  if (count of matches) is 0 then return "NOTFOUND"
  return body of item 1 of matches
end tell`;
  return execFileSync('osascript', ['-e', script], { encoding: 'utf8', timeout: 20_000 });
}

function toPlain(html) {
  return html
    .replace(/<br\s*\/?>/gi, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .trim();
}

const raw = getBody();
if (raw.trim() === 'NOTFOUND') {
  console.error(`「${NOTES_TITLE}」ノートが見つかりません`);
  process.exit(1);
}
if (/<(ul|ol|table)[ >]/i.test(raw)) {
  console.error('想定外の構造（リスト・表）を検出したため中止します。ノートを手動で確認してください。');
  process.exit(1);
}

const blocks = raw.match(/<div>[\s\S]*?<\/div>/g) || [];
if (!blocks.length) {
  console.error('<div> ブロックを検出できませんでした。ノートの構造を確認してください。');
  process.exit(1);
}
const plainBlocks = blocks.map(toPlain);

// ── 照合 ───────────────────────────────────────────────
const plan = [];
const problems = [];
for (const t of targets) {
  const hits = [];
  for (let i = 0; i < blocks.length; i++) if (plainBlocks[i] === t) hits.push(i);
  if (hits.length === 0) problems.push(`見つからない: ${t}`);
  else if (hits.length > 1) problems.push(`${hits.length} 箇所に同一行がある（曖昧）: ${t}`);
  else plan.push({ text: t, index: hits[0] });
}
if (problems.length) {
  console.error('\n中止します。以下を解消してください。');
  problems.forEach((p) => console.error(`  ✗ ${p}`));
  process.exit(1);
}

console.log(`\n--- 削除する ${plan.length} 行 ---`);
plan.forEach((p) => console.log(`  ${p.text}`));

if (!APPLY) {
  console.log('\ndry-run。--apply で削除します。');
  process.exit(0);
}

// ── 削除 → body を書き換え ───────────────────────────────
const removeIdx = new Set(plan.map((p) => p.index));
const newBlocks = blocks.filter((_, i) => !removeIdx.has(i));
const newBody = newBlocks.join('\n');

const tmpFile = join(tmpdir(), `notes-body-${Date.now()}.html`);
writeFileSync(tmpFile, newBody, 'utf8');
try {
  const setScript = `
set theFile to POSIX file "${tmpFile}"
set theText to read theFile as «class utf8»
tell application "Notes"
  set matches to every note whose name is "${NOTES_TITLE}"
  set body of item 1 of matches to theText
end tell`;
  execFileSync('osascript', ['-e', setScript], { encoding: 'utf8', timeout: 20_000 });
} finally {
  unlinkSync(tmpFile);
}

// 読み戻して消えたことを確認する
const after = getBody();
const afterPlain = (after.match(/<div>[\s\S]*?<\/div>/g) || []).map(toPlain);
const remaining = plan.filter((p) => afterPlain.includes(p.text));
if (remaining.length) {
  console.error(`\n⚠️  削除後も残っています（${remaining.length} 件）`);
  remaining.forEach((p) => console.error(`  ${p.text}`));
  process.exit(1);
}
console.log(`\n✅ ${plan.length} 行を Apple Notes「${NOTES_TITLE}」から削除し、読み戻して消えたことを確認しました`);
