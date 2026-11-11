#!/usr/bin/env node
/**
 * 済んでいると確かめた台帳の行を閉じ、archive へ退避し、各リポジトリで commit・push する。
 *
 *   node <エンジン>/scripts/close-items.mjs [--apply] < items.tsv   # インスタンスのフォルダで実行する
 *
 * 標準入力は1行1件のタブ区切り（非ASCIIを引数に載せない）：
 *   <~/Projects からの台帳のパス> <行番号> <その行に含まれるはずの文字列> <済んだ根拠>
 * 例：work/admin/action-items.md	59	山田さん	10/15 08:30 に送信済み
 *
 * 規則の正本は ../docs/design.md「書き込み方針」の「台帳の行を閉じる」。ここは手順だけを持つ。
 *   1. リポジトリごとに fetch し、遅れていれば fast-forward で追随する。手元の未コミットの変更と取り込む変更が
 *      同じファイルに触れるとき（2026-10-17 に autostash の衝突を起こした）、手元に未 push の commit があるとき、
 *      fast-forward できないときは、そのリポジトリには手を付けない
 *   2. 行が今も `- [ ]`・`- [!]` で、照合の文字列を含むかを確かめる。ずれていれば何もしない
 *   3. `- [x]` にして行末に根拠を足し、archive-action-items.py で退避する
 *   4. git 管理下の台帳だけ、その2ファイルを commit・push する。手元の他の変更は巻き込まない
 * 既定は dry-run。
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { execFileSync } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { instance, expandHome, PROJECTS } from './lib/instance.mjs';

const ARCHIVE = expandHome(instance().cfg.archiveScript);
const APPLY = process.argv.includes('--apply');
// Windows の Python の公式の導入は python3 を作らない（python と py だけ）
const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Tokyo' });

const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
const tryGit = (cwd, ...args) => {
  try { return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return null; }
};

const items = readFileSync(0, 'utf8').split('\n').filter((l) => l.trim()).map((l) => {
  const [path, line, match, evidence] = l.split('\t');
  return { path, line: Number(line), match, evidence };
});
if (!items.length) { console.error('標準入力が空です'); process.exit(1); }

// リポジトリの根を引く
for (const it of items) {
  it.abs = join(PROJECTS, it.path);
  if (!existsSync(it.abs)) { it.skip = 'ファイルが無い'; continue; }
  it.repo = tryGit(dirname(it.abs), 'rev-parse', '--show-toplevel');
  it.tracked = it.repo && tryGit(it.repo, 'ls-files', '--error-unmatch', it.abs) !== null;
}

// 1. 追随
const repos = [...new Set(items.filter((i) => i.repo && !i.skip).map((i) => i.repo))];
const blocked = new Set();
for (const repo of repos) {
  tryGit(repo, 'fetch', '-q');
  const behind = Number(tryGit(repo, 'rev-list', '--count', 'HEAD..@{u}') || 0);
  if (!behind) continue;
  const dirty = (tryGit(repo, 'diff', '--name-only') || '').split('\n').filter(Boolean);
  const incoming = (tryGit(repo, 'diff', '--name-only', 'HEAD', '@{u}') || '').split('\n').filter(Boolean);
  const overlap = dirty.filter((f) => incoming.includes(f));
  if (overlap.length) { blocked.add(repo); console.log(`⚠ ${repo}：${behind} コミット遅れ。手元の変更と重なる（${overlap.join(', ')}）ので触らない`); continue; }
  // 追随は fast-forward だけにする。pull --rebase --autostash は、手元の未 push の commit の積み直しか未コミットの
  // 編集の戻しで衝突すると、rebase の途中や衝突と stash を残したまま終わり、無人の処理が書く共有の作業コピーの
  // 以後の pull をすべて止める。手元に未 push の commit があれば、積み直しはその commit を作った処理に任せて触らない
  const ahead = Number(tryGit(repo, 'rev-list', '--count', '@{u}..HEAD') || 0);
  if (ahead) { blocked.add(repo); console.log(`⚠ ${repo}：${behind} コミット遅れ。手元に未 push の commit が ${ahead} 件あるので触らない`); continue; }
  if (APPLY) {
    try { git(repo, 'merge', '-q', '--ff-only', '@{u}'); console.log(`↓ ${repo}：${behind} コミット追随`); } catch (e) {
      blocked.add(repo); console.log(`⚠ ${repo}：${behind} コミット遅れ。fast-forward できない（${String(e.stderr || e.message).trim().split('\n')[0]}）ので触らない`);
    }
  }
  else console.log(`↓ ${repo}：${behind} コミット遅れ（--apply で追随する）`);
}

// 2・3. 行を閉じる
const byFile = new Map();
for (const it of items) {
  if (it.skip) continue;
  if (blocked.has(it.repo)) { it.skip = 'リポジトリが追随できない'; continue; }
  // 台帳そのものに未コミットの変更があれば、別のセッションが書いている途中かもしれないので触らない
  if (it.tracked && tryGit(it.repo, 'diff', '--name-only', '--', it.abs)) { it.skip = '台帳に未コミットの変更がある（別のセッションが編集中の可能性）'; continue; }
  // 同じファイルの2件目以降は、1件目で書き換えた行の上に重ねる（読み直すと先の書き換えが消える）
  // 改行は元のまま書き戻す（Windows の clone は core.autocrlf で CRLF）
  if (!byFile.has(it.abs)) { const t = readFileSync(it.abs, 'utf8'); byFile.set(it.abs, { eol: t.includes('\r\n') ? '\r\n' : '\n', lines: t.split(/\r?\n/), items: [] }); }
  const { lines } = byFile.get(it.abs);
  const cur = lines[it.line - 1] ?? '';
  if (!/^\s*- \[[ !]\]/.test(cur)) { it.skip = `${it.line}行が未完了の行でない`; continue; }
  if (!cur.includes(it.match)) { it.skip = `${it.line}行に「${it.match}」が無い（行がずれた）`; continue; }
  lines[it.line - 1] = cur.replace(/- \[[ !]\]/, '- [x]').trimEnd() + ` 済み：${it.evidence}（${instance().cfg.name} が ${today} に確認）`;
  byFile.get(it.abs).items.push(it);
}
for (const it of items) if (it.skip) console.log(`✗ ${it.path} ${it.line}行：${it.skip}`);
for (const [abs, f] of byFile) for (const it of f.items) console.log(`✓ ${it.path} ${it.line}行を閉じる：${it.evidence}`);
if (!APPLY) { console.log('dry-run。--apply で実行します。'); process.exit(0); }

const touched = new Map();
for (const [abs, f] of byFile) {
  if (!f.items.length) continue;
  writeFileSync(abs, f.lines.join(f.eol));
  execFileSync(PYTHON, [ARCHIVE, abs], { stdio: 'inherit' });
  const it = f.items[0];
  if (!it.tracked) { console.log(`・${it.path} は git 管理外。退避までで止める`); continue; }
  if (!touched.has(it.repo)) touched.set(it.repo, []);
  const arch = join(dirname(abs), 'action-items-archive.md');
  touched.get(it.repo).push(abs, ...(existsSync(arch) ? [arch] : []));
}

// 4. commit・push
for (const [repo, files] of touched) {
  git(repo, 'add', '--', ...files);
  git(repo, 'commit', '-q', '-m', `${instance().cfg.name}: 済んでいた行を閉じて退避する\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, '--', ...files);
  const out = tryGit(repo, 'push', '-q');
  console.log(out === null ? `⚠ ${repo}：push に失敗。手で push する` : `↑ ${repo}：commit・push した`);
}

// 閉じて退避すると後ろの行の番号が詰まるので、today.md の参照をその場で合わせる
try {
  execFileSync(process.execPath, [fileURLToPath(new URL('./check-today.mjs', import.meta.url)), '--fix'], { stdio: 'inherit' });
} catch { console.log('⚠ check-today.mjs が問題を報せた。上の出力を見て today.md を直す'); }
