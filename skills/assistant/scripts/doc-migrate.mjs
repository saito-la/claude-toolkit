#!/usr/bin/env node
/**
 * doc-migrate.mjs — 作業用の文書の現役ゾーン（捕捉＋直近ToDo）の項目を、実際に各プロジェクトの
 * action-items.md へ書き込み、書き込みが確認できたものだけ作業用の文書から削除する。
 *
 * docs/design.md「書き込み方針」の「各プロジェクトの action-items.md への移送」（2026-08-01 承認）。
 * 移送の経緯はインスタンスの docs/migration-log.md に残す。
 *
 * 処理順序（自己整合性のため厳守）：
 *   1. doc-remove.mjs の照合ロジックで、削除予定行が現役ゾーンに一意に存在するか dry-run 確認
 *   2. 確認が通ったものだけ、各 dest ファイルへ追記（見出し「## <文書の呼び名>から移送 YYYY-MM-DD」配下）
 *   3. インスタンスの docs/migration-log.md に追記（移送の経緯を追える監視用の正本）
 *   4. doc-remove.mjs --apply で作業用の文書から削除し、読み戻して消えたことを確認
 *
 * Usage（インスタンスのフォルダで実行する）:
 *   node doc-migrate.mjs --plan <plan.json>          # dry-run（何もしない。判定だけ表示）
 *   node doc-migrate.mjs --plan <plan.json> --apply
 *
 * plan.json の形式：
 *   [
 *     { "lines": ["段落1", "段落2", ...], "dest": "work/admin/action-items.md", "label": "短い説明" }
 *   ]
 *   lines は作業用の文書の該当ブロックを構成する段落テキストをそのまま列挙する（完全一致で照合）。
 */
import { readFileSync, writeFileSync, existsSync, unlinkSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';
import { instance, PROJECTS as PROJECTS_ROOT } from './lib/instance.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const INST = instance();
const DOC = INST.cfg.workDoc.name;
const TODAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());

const argv = process.argv.slice(2);
const APPLY = argv.includes('--apply');
const planArg = (() => {
  const i = argv.indexOf('--plan');
  return i !== -1 ? argv[i + 1] : null;
})();
if (!planArg) {
  console.error('--plan <plan.json> を指定してください。');
  process.exit(1);
}
const plan = JSON.parse(readFileSync(planArg, 'utf8'));

// ── 1. dest ファイルの存在確認 ───────────────────────────
for (const item of plan) {
  const dest = join(PROJECTS_ROOT, item.dest);
  if (!existsSync(dest)) {
    console.error(`✗ dest が存在しません: ${item.dest}`);
    process.exit(1);
  }
}

// ── 2. doc-remove.mjs で dry-run 確認（現役ゾーンに一意に存在するか） ─────
const allLines = plan.flatMap((item) => item.lines);
mkdirSync(INST.file('data'), { recursive: true });
const tmpLinesFile = INST.file('data', `.migrate-lines-${Date.now()}.txt`);
writeFileSync(tmpLinesFile, allLines.join('\n'));
process.on('exit', () => { try { unlinkSync(tmpLinesFile); } catch {} });

console.log('--- doc-remove.mjs dry-run で現役ゾーンの一意性を確認 ---');
try {
  const out = execFileSync('node', [join(HERE, 'doc-remove.mjs'), '--lines-file', tmpLinesFile], { encoding: 'utf8' });
  console.log(out);
} catch (e) {
  console.error(e.stdout || '');
  console.error(e.stderr || '');
  console.error(`\n中止します。${DOC} 側の照合に失敗した行があります（上記参照）。dest への書き込みは行っていません。`);
  process.exit(1);
}

// ── 3. 追記プランを表示 ───────────────────────────────────
console.log('\n--- 追記プラン ---');
for (const item of plan) {
  console.log(`+ ${item.dest}（${item.label}）`);
  console.log(`    - [ ] ${item.lines.join(' / ')}（${DOC}捕捉ゾーンより移送 ${TODAY}）`);
}

if (!APPLY) {
  console.log('\ndry-run。--apply で実際に書き込み・削除します。');
  process.exit(0);
}

// ── 4. dest へ追記（見出しが無ければ作る。既にあれば直下に足す） ─────
const HEADING = `## ${DOC}から移送 ${TODAY}`;
for (const item of plan) {
  const dest = join(PROJECTS_ROOT, item.dest);
  let text = readFileSync(dest, 'utf8');
  const bullet = `- [ ] ${item.lines.join(' / ')}（${DOC}捕捉ゾーンより移送 ${TODAY}）\n`;
  if (text.includes(HEADING)) {
    // 見出しブロックの末尾（次の「## 」見出しの直前、無ければファイル末尾）に追記する。
    // 見出し直後に挿すと、複数件を順に追記したとき最後に処理した行が先頭に来て順序が崩れる。
    const idx = text.indexOf(HEADING);
    const headingLineEnd = text.indexOf('\n', idx) + 1;
    const nextHeadingIdx = text.indexOf('\n## ', headingLineEnd);
    const insertAt = nextHeadingIdx === -1 ? text.length : nextHeadingIdx;
    text = text.slice(0, insertAt) + bullet + text.slice(insertAt);
  } else {
    if (!text.endsWith('\n')) text += '\n';
    text += `\n${HEADING}\n\n${bullet}`;
  }
  writeFileSync(dest, text);
  console.log(`✅ ${item.dest} に追記しました`);
}

// ── 5. migration-log.md に追記 ────────────────────────────
const logPath = INST.file('docs', 'migration-log.md');
mkdirSync(dirname(logPath), { recursive: true });
let log = existsSync(logPath) ? readFileSync(logPath, 'utf8') : '';
if (!log.includes(`## ${TODAY}`)) {
  if (!log.endsWith('\n') && log) log += '\n';
  log += `\n## ${TODAY}\n\n`;
}
const insertAt = log.indexOf(`## ${TODAY}`);
const afterHeadingLineEnd = log.indexOf('\n', insertAt) + 1;
let entries = '';
for (const item of plan) {
  entries += `- 捕捉ゾーン → \`${item.dest}\`：${item.label}\n`;
}
log = log.slice(0, afterHeadingLineEnd + 1) + entries + log.slice(afterHeadingLineEnd + 1);
writeFileSync(logPath, log);
console.log(`✅ ${logPath} に記録しました`);

// ── 6. doc-remove.mjs --apply で作業用の文書から削除 ─────────────
console.log(`\n--- doc-remove.mjs --apply で ${DOC} から削除 ---`);
const out = execFileSync('node', [join(HERE, 'doc-remove.mjs'), '--lines-file', tmpLinesFile, '--apply'], { encoding: 'utf8' });
console.log(out);

console.log(`\n✅ 移送完了：${plan.length} 件`);
