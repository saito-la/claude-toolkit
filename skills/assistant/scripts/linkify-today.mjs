#!/usr/bin/env node
/**
 * today.md の下書きの印 ‹<台帳>:<行>› を、クリックでそのプロジェクトの claude が開く pj リンクに替える。
 *
 *   node <エンジン>/scripts/linkify-today.mjs [today.md]   # インスタンスのフォルダで実行する
 *
 * 設定の features.pjLinks が true のときだけ動く。リンクを作る道具は設定の pjLinks.link。
 * 1項目の中の印はプロジェクトごとに1本のリンクにまとめ、項目の末尾に置く。ラベルはプロジェクトの短い名前。
 * リンクを押すと、項目の本文を最初の依頼文として claude が起動する（受け口と署名は
 * 設定の pjLinks.link の道具が持つ）。依頼文には行番号ではなく台帳の行の本文の頭を入れる。
 * 行番号は台帳が閉じられると詰まってずれるが、依頼文は署名の対象なので後から書き換えられないため。
 * 行番号は URL の l に持ち、check-today.mjs が照合し --fix で直す。
 * 規則の正本は ../docs/design.md「出力」。
 */
import { readFileSync, writeFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { refsOf, projectOf, PROJECTS } from './today-refs.mjs';
import { instance, requireFeature, need, expandHome } from './lib/instance.mjs';

const INST = instance();
requireFeature('pjLinks', 'pj リンク');
const PJ_LINK = expandHome(need('pjLinks.link'));
const target = process.argv[2] || INST.file('today.md');
const lines = readFileSync(target, 'utf8').split('\n');
const day = (lines[0] || '').replace(/^#\s*/, '').trim();

const jobs = [];   // pj-link.py へ渡す行
const slots = [];  // { li, project } … 置き換える場所
// 節ごとの依頼の言い方
const ASK = {
  'やること': '次の項目を進めてください',
  '直近の締切': '期日までに次の行動を済ませたいので、進めてください',
  '台帳の不整合': '次の台帳の不整合を直してください',
  '他人ボール': '次の件は相手の返事を待っています。督促・フォローが要るか確かめ、要れば進めてください',
};
let section = '';
lines.forEach((text, li) => {
  const h = text.match(/^## (.+)$/);
  if (h) { section = h[1].trim(); return; }
  if (!text.includes('‹')) return;
  const marks = refsOf(text).filter((r) => r.kind === 'mark');
  if (!marks.length) return;
  // 項目名の行の下に action を箇条書きで置く書き方（design.md「今日の一覧の書き方」）。下の行も依頼文に入れる
  const acts = [];
  for (let k = li + 1; k < lines.length && /^\s+(-|\d+\.)\s/.test(lines[k]); k++) acts.push(lines[k].replace(/^\s+(-|\d+\.)\s+/, '').replace(/\s*‹[^›]*›/g, '').trim());
  const body = [text.replace(/\s*‹[^›]*›/g, '').replace(/^(\d+\.|-)\s+/, '').trim(), ...acts].join(' ／ ');
  const byProject = new Map();
  for (const { path, n } of marks) {
    const project = projectOf(path);
    if (!byProject.has(project)) byProject.set(project, new Map());
    const files = byProject.get(project);
    const file = path.slice(project.length + 1);
    if (!files.has(file)) files.set(file, []);
    files.get(file).push(n);
  }
  for (const [project, files] of byProject) {
    // 1つのリンクが持てる台帳は1つ。同じプロジェクトで台帳が2つあるときは最初のものを URL に、残りは依頼文だけに入れる
    const [[file, ns], ...rest] = [...files];
    const quote = (f, list) => {
      const rows = readFileSync(`${PROJECTS}/${project}/${f}`, 'utf8').split(/\r?\n/);
      return list.slice(0, 3).map((n) => `「${(rows[n - 1] || '').replace(/^\s*- \[[ !x]\]\s*/, '').replace(/\*\*/g, '').slice(0, 50)}…」`).join('');
    };
    const ledger = [[file, ns], ...rest].map(([f, list]) => `${f} の ${quote(f, list)}${list.length > 3 ? ` ほか${list.length - 3}行` : ''}`).join('、');
    const prompt = `${INST.cfg.name} の today.md（${day}）の「${section}」から。${ASK[section] || '次の項目を進めてください'}：${body}\n台帳は ${ledger}。`;
    const label = project.split('/').pop();
    jobs.push([project, label, prompt.replace(/\t/g, ' ').replace(/\n/g, ' '), file, ns.join(',')].join('\t'));
    slots.push({ li });
  }
});
if (!jobs.length) { console.log('印 ‹…› は無かった'); process.exit(0); }

const links = execFileSync('python3', [PJ_LINK], { input: jobs.join('\n') + '\n', encoding: 'utf8' }).trim().split('\n');
const add = new Map();
slots.forEach((s, i) => { if (!add.has(s.li)) add.set(s.li, []); add.get(s.li).push(links[i]); });
for (const [li, ls] of add) lines[li] = `${lines[li].replace(/\s*‹[^›]*›/g, '')} ${ls.join(' ')}`;
writeFileSync(target, lines.join('\n'));
console.log(`✅ ${links.length} 本のリンクに替えた`);
