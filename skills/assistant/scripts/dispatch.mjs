#!/usr/bin/env node
/**
 * インスタンスで受けた作業を、そのプロジェクトの claude に依頼文付きで開いて始めさせる。
 *
 *   node <エンジン>/scripts/dispatch.mjs --item 7 [--pick <プロジェクトの短い名前>] [--project <relpath>] [--dry-run] [< 追加の指示]
 *   node <エンジン>/scripts/dispatch.mjs --project work/project-a [--dry-run] < 依頼文
 * インスタンスのフォルダで実行する。設定の features.pjLinks が true のときだけ動く。
 *
 * --item N は today.md の「やること」の N 番を使う。項目の pj リンク（linkify-today.mjs が作る）から
 * プロジェクトと依頼文を取る。リンクが複数あれば最初のものを使い、--pick で選び直せる。
 * リンクの無い項目（メールだけが出所の項目）は --project が要り、依頼文は項目の本文から組み立てる。
 * 標準入力は必須で、「理由」の節（なぜこの依頼を出すことになったか）を含める。無ければ開かずに止まる。
 * --item では項目の本文の後ろに足し、--project だけのときは依頼文そのものにする。
 * 非ASCIIは引数に載せない（壊れる端末があるため）。
 *
 * 開くのは設定の pjLinks.openに --prompt-file を付けたもの。
 * 新しい iTerm2 のウィンドウでそのプロジェクトの worktree に claude が起動し、依頼文を最初の指示として受け取る。
 * 振り分けの決まりの正本はインスタンスの CLAUDE.md「指示の振り分け」。
 */
import { readFileSync, writeFileSync, mkdtempSync, fstatSync } from 'fs';
import { execFileSync } from 'child_process';
import { tmpdir } from 'os';
import { join } from 'path';
import { instance, requireFeature, need, expandHome } from './lib/instance.mjs';

const INST = instance();
requireFeature('pjLinks', '作業の振り分け');
const PJ_OPEN = expandHome(need('pjLinks.open'));
const TODAY = INST.file('today.md');
const NAME = INST.cfg.name;

const argv = process.argv.slice(2);
const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv[i + 1]; };
const ITEM = opt('--item');
const PICK = opt('--pick');
let PROJECT = opt('--project');
const DRY = argv.includes('--dry-run');
if (!ITEM && !PROJECT) {
  console.error('使い方: dispatch.mjs --item <番号> | --project <relpath>（依頼文は標準入力）');
  process.exit(1);
}

// 標準入力は、パイプかファイルで渡されたときだけ読む（端末なら待たない）
let stdinText = '';
try {
  const st = fstatSync(0);
  if (st.isFIFO() || st.isFile()) stdinText = readFileSync(0, 'utf8').trim();
} catch { /* 標準入力なし */ }

// 依頼を出すことになった理由を必ず添える（2026-10-23 決定）。開いた先は依頼元の文脈を持たない
if (!/理由/.test(stdinText)) {
  console.error('標準入力に「理由」の節が無い。なぜこの依頼を出すことになったか（きっかけ・なぜ今か・確かめたこと・決まっていること）を書いて渡す');
  process.exit(1);
}

let prompt = '';
if (ITEM) {
  const lines = readFileSync(TODAY, 'utf8').split('\n');
  const day = (lines[0] || '').replace(/^#\s*/, '').trim();
  let section = '';
  let line = null;
  for (const text of lines) {
    const h = text.match(/^## (.+)$/);
    if (h) { section = h[1].trim(); continue; }
    if (section === 'やること' && text.startsWith(`${ITEM}. `)) { line = text; break; }
  }
  if (!line) { console.error(`today.md の「やること」に ${ITEM} 番が無い`); process.exit(1); }

  const links = [...line.matchAll(/\[([^\]]+)\]\((pj:\/\/open\?[^)]+)\)/g)].map(([, label, url]) => {
    const q = new URL(url).searchParams;
    return { label, project: q.get('d'), prompt: q.get('p') };
  });
  if (links.length > 1) console.error(`この項目のリンク：${links.map((l) => `${l.label}（${l.project}）`).join('、')}`);
  const chosen = PICK ? links.find((l) => l.label === PICK || l.project === PICK) : links[0];
  if (PICK && !chosen) { console.error(`--pick ${PICK} に当たるリンクが無い`); process.exit(1); }

  if (chosen && !PROJECT) {
    PROJECT = chosen.project;
    prompt = chosen.prompt;
  } else {
    if (!PROJECT) { console.error('この項目には pj リンクが無い。--project <relpath> で開く先を指定する'); process.exit(1); }
    // メールのリンクは「ラベル（URL）」にして、開いた先から辿れるようにする
    const body = line
      .replace(/^\d+\.\s+/, '')
      .replace(/\[([^\]]+)\]\((pj:\/\/[^)]+)\)/g, '')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '$1（$2）')
      .trim();
    prompt = `${NAME} の today.md（${day}）の「やること」${ITEM} から。次の項目を進めてください：${body}`;
  }
  prompt += `\n\n${stdinText}`;
} else {
  prompt = stdinText;
}

console.log(`開く先：${PROJECT}`);
console.log(`依頼文：${prompt}`);
if (DRY) { console.log('dry-run。開かずに終える'); process.exit(0); }

const dir = mkdtempSync(join(tmpdir(), 'assistant-dispatch-'));
const file = join(dir, 'prompt.txt');
writeFileSync(file, prompt + '\n');
execFileSync('bash', [PJ_OPEN, PROJECT, '--prompt-file', file], { stdio: 'inherit' });
