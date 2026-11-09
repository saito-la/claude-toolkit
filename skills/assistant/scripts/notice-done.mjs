#!/usr/bin/env node
/**
 * 勤務先への連絡を済ませたことを、インスタンスの attendance-notices.csv に残す。
 *
 *   node <エンジン>/scripts/notice-done.mjs --date 2026-10-30 --via chat [--rule <規則の label>] [--apply] [< 備考]
 *
 * Chat や口頭で伝えた連絡はメールに痕跡が残らず、collect.mjs が読めない。使い手が「済ませた」と言ったら、
 * その日をここに書いて、翌日から「やること」に出さないようにする。メールで送った分は送信済みから拾うので書かない。
 * 判定の正本は notices.mjs、運用は ../docs/design.md「勤務先への連絡」。
 *
 * --date は複数並べてよい（--date 2026-10-30 --date 2026-11-06）。--rule は規則が1つなら省いてよい。
 * 備考は標準入力の1行目（非ASCIIを引数に載せないため）。既定は dry-run。書いた後の commit・push は
 * today.md と同じく、インスタンスが git に入れているなら呼び手が行う。
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { instance } from './lib/instance.mjs';
import { loadMarks, MARKS_FILE, MARKS_HEAD, dayLabel } from './notices.mjs';

const INST = instance();
const argv = process.argv.slice(2);
const all = (name) => argv.flatMap((a, i) => (a === name ? [argv[i + 1]] : []));
const APPLY = argv.includes('--apply');
const dates = all('--date');
const via = all('--via')[0];
const rules = INST.cfg.attendanceNotices;
let rule = all('--rule')[0];

if (!rules.length) { console.error('assistant.json に attendanceNotices が無い'); process.exit(1); }
if (!dates.length || dates.some((d) => !/^\d{4}-\d{2}-\d{2}$/.test(d))) { console.error('--date YYYY-MM-DD を1つ以上渡す'); process.exit(1); }
if (!via) { console.error('--via（chat・mail・口頭 など、伝えた手段）を渡す'); process.exit(1); }
if (!rule) {
  if (rules.length > 1) { console.error(`規則が複数あるので --rule を渡す（${rules.map((r) => r.label).join('・')}）`); process.exit(1); }
  rule = rules[0].label;
} else if (!rules.some((r) => r.label === rule)) { console.error(`規則 ${rule} が無い`); process.exit(1); }

const note = process.stdin.isTTY ? '' : (readFileSync(0, 'utf8').split('\n')[0] || '').replace(/,/g, '、').trim();
const file = INST.file(MARKS_FILE);
const marks = loadMarks(file);
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Tokyo' });
const add = dates.filter((d) => !marks.some((m) => m.date === d && m.rule === rule));
for (const d of dates) console.log(`${add.includes(d) ? '足す' : '既にある'}: ${dayLabel(d)} ${rule} ${via}${note ? ` ${note}` : ''}`);
if (!add.length) process.exit(0);
if (!APPLY) { console.log('dry-run。--apply で書く'); process.exit(0); }
const body = existsSync(file) ? readFileSync(file, 'utf8').replace(/\n*$/, '\n') : `﻿${MARKS_HEAD}\n`;
writeFileSync(file, body + add.map((d) => `${d},${rule},${via},${today},${note}`).join('\n') + '\n');
console.log(`✅ ${file} に ${add.length} 行を足した`);
