#!/usr/bin/env node
/**
 * today.md に書いた人名漢字が、一次ソースに逐字で存在するかを点検する。
 *
 *   node <エンジン>/scripts/check-names.mjs [today.md]   # インスタンスのフォルダで実行する
 *
 * 設定の features.checkNames が true のときだけ動く。
 * 「〇〇さん／先生／様」の〇〇（漢字）を拾い、次のどれかに同じ文字列が無ければ警告して終了コード1を返す。
 *   - data/candidates.json（作業用の文書＝使い手が書いた本文、メールの From 表示名、台帳の本文）
 *   - 設定の checkNames.sources に挙げた名簿（組織の名簿の CSV など）
 *   - 設定の checkNames.wikiEntities（個人の知識ベースの人物ページ。frontmatter に aliases と sources を持つ md）のうち sources が空でないものの aliases
 * ローマ字の From（Taro Yamada 等）から漢字を起こした名前は、どこにも逐字で無いのでここで落ちる。
 * 前回の today.md は照合先に入れない——二次記述の再利用が当て字を広げる経路だったため。
 */
import { readFileSync, readdirSync, existsSync } from 'fs';
import { instance, requireFeature, expandHome } from './lib/instance.mjs';

const INST = instance();
requireFeature('checkNames', '人名漢字の点検');
const target = process.argv[2] || INST.file('today.md');
const text = readFileSync(target, 'utf8');

const corpus = [readFileSync(INST.file('data', 'candidates.json'), 'utf8')];
for (const src of INST.cfg.checkNames.sources.map(expandHome)) if (existsSync(src)) corpus.push(readFileSync(src, 'utf8'));
const entDir = expandHome(INST.cfg.checkNames.wikiEntities);
if (entDir && existsSync(entDir)) {
  for (const f of readdirSync(entDir)) {
    if (!f.endsWith('.md')) continue;
    const head = readFileSync(`${entDir}/${f}`, 'utf8').split('\n---')[0];
    const src = head.match(/^sources:\s*(.*)$/m);
    if (!src || /^\[\s*\]$/.test(src[1].trim()) || src[1].trim() === '') continue;
    const al = head.match(/^aliases:\s*(.*)$/m);
    if (al) corpus.push(al[1]);
  }
}
const all = corpus.join('\n').replace(/[ 　]/g, '');

const NAME = /([一-龥々]{1,3}(?:[ 　]?[一-龥々]{1,3})?)(?=さん|先生|様)/g;
const bad = new Map();
text.split('\n').forEach((line, i) => {
  for (const m of line.matchAll(NAME)) {
    const name = m[1].replace(/[ 　]/g, '');
    if (!all.includes(name)) bad.set(name, [...(bad.get(name) || []), i + 1]);
  }
});

if (!bad.size) { console.log('✅ 人名漢字はすべて一次ソースに逐字で存在します'); process.exit(0); }
console.log('⚠ 一次ソースに無い人名漢字（ローマ字の From から起こしていないか確認し、表示名のまま書く）:');
for (const [n, lines] of bad) console.log(`  ${n}  （${target.split('/').pop()} ${lines.join(',')}行）`);
process.exit(1);
