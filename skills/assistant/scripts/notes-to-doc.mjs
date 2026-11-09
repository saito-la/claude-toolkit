#!/usr/bin/env node
/**
 * notes-to-doc.mjs — Apple Notes の捕捉用のノート（設定の appleNotes.title）の内容を、作業用の文書の
 * `# Capture` ゾーンへ転記し、転記が確認できた行だけ Notes から削除する。
 * 設定の features.appleNotes が true のときだけ動く。開始時の収集（session-start.mjs）が collect.mjs の前に流す。
 *
 * 2026-08-01 決定：Notes はレビュー対象にしない。iPhone で Google Docs（作業用の文書）を
 * 開くと重く、移動中に思いついたことをサッと書けないため、Notes は「高速捕捉専用の入口」
 * として残し、レビューは作業用の文書の `# Capture` 1箇所に集約する。docs/design.md「入力源」参照。
 *
 * 処理順序（doc-migrate.mjs と同じ型）：
 *   1. Notes の捕捉用のノートから転記対象の行を取り出す
 *   2. notes-remove.mjs の dry-run で、Notes 側で一意に削除できるか確認
 *   3. 確認が通ったら、作業用の文書の `# Capture` ゾーン先頭（見出し直後）へ追記する。
 *      Notes は移動中の新しいネタが多いため、末尾より先頭に置く（2026-08-01 決定）
 *   4. 追記を読み戻して確認できたら、notes-remove.mjs --apply で Notes から削除
 *
 * Usage（インスタンスのフォルダで実行する）:
 *   node notes-to-doc.mjs             # dry-run（何もしない。判定だけ表示）
 *   node notes-to-doc.mjs --apply
 */
import { writeFileSync, unlinkSync } from 'fs';
import { execFileSync } from 'child_process';
import { tmpdir } from 'os';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { instance, requireFeature, need } from './lib/instance.mjs';
import { accessToken as tokenOf, getDoc, batchUpdate, paragraphs } from './lib/google.mjs';

requireFeature('appleNotes', 'Apple Notes の取り込み');
const HERE = dirname(fileURLToPath(import.meta.url));
const CFG = instance().cfg;
const NOTES_TITLE = CFG.appleNotes.title;
const DOC = CFG.workDoc.name;
const DOC_ID = need('workDoc.id');
const CRED = need('workDoc.credential');
const CAPTURE_HEADING = '# Capture';
const BACKLOG_HEADING = '# Backlog';
const APPLY = process.argv.includes('--apply');

// ── Notes 側（notes-remove.mjs と同じ抽出ロジック） ─────────
function getNotesBody() {
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

const raw = getNotesBody();
if (raw.trim() === 'NOTFOUND') {
  console.error(`「${NOTES_TITLE}」ノートが見つかりません`);
  process.exit(1);
}
if (/<(ul|ol|table)[ >]/i.test(raw)) {
  console.error('想定外の構造（リスト・表）を検出したため中止します。ノートを手動で確認してください。');
  process.exit(1);
}
const blocks = raw.match(/<div>[\s\S]*?<\/div>/g) || [];
const items = [...new Set(blocks.map(toPlain).filter(Boolean).filter((t) => t !== NOTES_TITLE))];

if (!items.length) {
  console.log(`Notes の ${NOTES_TITLE} に転記対象がありません。`);
  process.exit(0);
}

console.log(`--- Notes → ${DOC}「${CAPTURE_HEADING}」へ転記する ${items.length} 行 ---`);
items.forEach((t) => console.log(`  + ${t}`));

// ── notes-remove.mjs の dry-run で、Notes 側で一意に削除できるか先に確認 ─────
const tmpLinesFile = join(tmpdir(), `notes-to-doc-lines-${Date.now()}.txt`);
writeFileSync(tmpLinesFile, items.join('\n'));
const cleanup = () => { try { unlinkSync(tmpLinesFile); } catch {} };

console.log('\n--- notes-remove.mjs dry-run で Notes 側の一意性を確認 ---');
try {
  const out = execFileSync('node', [join(HERE, 'notes-remove.mjs'), '--lines-file', tmpLinesFile], { encoding: 'utf8' });
  console.log(out);
} catch (e) {
  console.error(e.stdout || '');
  console.error(e.stderr || '');
  console.error(`\n中止します。Notes 側の照合に失敗した行があります。${DOC} への書き込みは行っていません。`);
  cleanup();
  process.exit(1);
}

if (!APPLY) {
  console.log('dry-run。--apply で実際に転記・削除します。');
  cleanup();
  process.exit(0);
}

// ── 作業用の文書の側：Docs API で `# Capture` ゾーンの先頭（見出しの直後）に追記 ─────
async function getParagraphs(token) {
  return paragraphs(await getDoc(DOC_ID, token)).map(({ start, end, h1, text }) => ({ start, end, h1, text: text.trim() }));
}
const findH1 = (paras, label) => paras.findIndex((p) => p.h1 && p.text === label);

const token = await tokenOf(CRED);
let paras = await getParagraphs(token);
const ci = findH1(paras, CAPTURE_HEADING);
const bi = findH1(paras, BACKLOG_HEADING);
if (ci === -1) throw new Error(`「${CAPTURE_HEADING}」が見つかりません`);
if (bi === -1) throw new Error(`「${BACKLOG_HEADING}」が見つかりません`);
if (bi <= ci) throw new Error(`「${BACKLOG_HEADING}」が「${CAPTURE_HEADING}」より前にあります`);
const insertAt = paras[ci].end; // # Capture 見出しの直後（新しいネタが多いためゾーンの先頭に置く）

const insertText = items.map((t) => `${t}\n`).join('');
// insertText は挿入位置の段落スタイルを継承する。ここは # Backlog 見出しの直前のため、
// 明示しないと挿入した行が HEADING_1 を引き継いでしまう（2026-08-01 実例で発覚）。
const styleRange = { startIndex: insertAt, endIndex: insertAt + insertText.length };
await batchUpdate(DOC_ID, token, [
  { insertText: { location: { index: insertAt }, text: insertText } },
  { updateParagraphStyle: { range: styleRange, paragraphStyle: { namedStyleType: 'NORMAL_TEXT' }, fields: 'namedStyleType' } },
  { updateTextStyle: { range: styleRange, textStyle: {}, fields: 'bold,fontSize,link,underline,foregroundColor' } },
]);
console.log(`\n✅ ${DOC} の「${CAPTURE_HEADING}」ゾーンへ ${items.length} 行を追記しました`);

// 読み戻して確認
paras = await getParagraphs(token);
const afterTexts = new Set(paras.map((p) => p.text));
const missing = items.filter((t) => !afterTexts.has(t));
if (missing.length) {
  console.error(`\n⚠️  ${DOC} への追記が確認できない行があります。Notes は削除しません：`);
  missing.forEach((t) => console.error(`  ${t}`));
  cleanup();
  process.exit(1);
}

// ── Notes から削除（notes-remove.mjs --apply） ────────────
console.log('\n--- notes-remove.mjs --apply で Notes から削除 ---');
const out = execFileSync('node', [join(HERE, 'notes-remove.mjs'), '--lines-file', tmpLinesFile, '--apply'], { encoding: 'utf8' });
console.log(out);
cleanup();

console.log(`\n✅ 転記完了：${items.length} 行`);
