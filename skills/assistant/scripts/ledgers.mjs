/**
 * ledgers.mjs — 全プロジェクトの台帳（action-items.md）を探し、返事待ちの行を読む
 *
 * collect.mjs と インスタンスの外の道具（受信メールの自動処理など）の両方が使う。
 * 外の道具はインスタンスの外から import するので、ここは設定（assistant.json）を読まない。
 * 返事待ちの行の書式の正本は claude-toolkit の conventions/action-items-convention.md「返事待ちの行」で、
 * 機械で読む側の正本はこのファイル。読む側を2か所に持たないため、外の道具はここを import する。
 */
import { readFileSync, readdirSync, statSync } from 'fs';
import { execFileSync } from 'child_process';
import { homedir } from 'os';
import { basename, dirname, join, relative, sep } from 'path';

// 台帳のパスは today.md の参照と pj リンクに使うので、Windows でも / で区切る
const relPath = (from, to) => relative(from, to).split(sep).join('/');

export const PROJECTS = join(homedir(), 'Projects');
// 台帳のファイル名。規約（action-items-convention.md）は対象ごとに分けた <topic>_action_items.md も認める
export const isLedgerName = (name) => name === 'action-items.md' || /^[^.]+_action_items\.md$/.test(name);
// 台帳が存在しないため対象外（docs/design.md「入力源」参照）
// .claude は worktree（.claude/worktrees/<名前>/）の写しを持つので読まない
const LEDGER_SKIP_DIRS = new Set(['node_modules', '.git', '.claude', 'attachments', 'snapshots']);
// ~/Projects/<組織>/<部署>/<区分>/<案件>/action-items.md の深さまで読む。4 だった間は深い場所の台帳が1つも読まれていなかった（2026-10-21 に発覚）
const MAX_DEPTH = 6;

export function walkLedgers(dir = PROJECTS, depth = 1, out = []) {
  if (depth > MAX_DEPTH) return out;
  let entries;
  try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    if (e.isDirectory()) {
      if (LEDGER_SKIP_DIRS.has(e.name)) continue;
      walkLedgers(join(dir, e.name), depth + 1, out);
    } else if (isLedgerName(e.name)) {
      out.push(join(dir, e.name));
    }
  }
  return out;
}

// 台帳を行に分ける。Windows の clone は core.autocrlf で改行が CRLF になり、'\n' で分けると行末に '\r' が残って、
// 行末を $ で受ける正規表現（`- [ ] (.+)$`）が1行も当たらない。2026-11-12、311C4W991 で台帳35本・約700行が未処理0件として読まれた
export const readLines = (p) => readFileSync(p, 'utf8').split(/\r?\n/);

// 台帳を最後に変えた時刻。ファイルの mtime は clone・checkout・pull のたびに新しくなり、止まっている台帳も新しく見える。
// git 管理下で手元に変更が無ければ最後の commit の時刻、変更があるか git 管理外なら mtime を使う
const gitOut = (args) => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
const dirtyByTop = new Map();
function dirtyFiles(dir) {
  let top;
  try { top = gitOut(['-C', dir, 'rev-parse', '--show-toplevel']).trim(); } catch { return null; }
  if (!dirtyByTop.has(top)) {
    const set = new Set();
    try {
      for (const e of gitOut(['-C', top, 'status', '--porcelain', '-z']).split('\0')) if (e.length > 3) set.add(join(top, e.slice(3)).toLowerCase());
    } catch { /* 読めなければ commit 済みとみなす */ }
    dirtyByTop.set(top, set);
  }
  return dirtyByTop.get(top);
}
export function lastChanged(p) {
  const mtime = statSync(p).mtime;
  const dirty = dirtyFiles(dirname(p));
  if (!dirty || dirty.has(join(p).toLowerCase())) return mtime;
  try {
    const ct = gitOut(['-C', dirname(p), 'log', '-1', '--format=%ct', '--', basename(p)]).trim();
    return ct ? new Date(Number(ct) * 1000) : mtime;
  } catch { return mtime; }
}

// 返事待ちの行。「待ち：<アドレス>[、<アドレス>…] <最終の連絡日> <N>回目」に続けて、返事が来たときの次の一手を
// 「次：」の後に並べて書ける。「下書き <Gmail のメッセージID>」はそのメールへの返信の下書き（ID を省くと新しいメール）で、
// 1行に複数並べてよい。「実行 <台帳のフォルダからの相対パス>」は下書きの前に走らせるスクリプト（届いたメールの本文を
// 標準入力で受け、標準出力を下書きの材料にする）。次の一手は呼び手の道具が実行する。
const WAIT_RE = /待ち：\s*((?:`?[^\s`,、]+@[^\s`,、]+`?\s*[,、]\s*)*`?[^\s`,、]+@[^\s`,、]+`?)\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d+)回目)?/;
const NEXT_RE = /次：((?:\s*(?:下書き(?:\s+`?[0-9a-f]{16}`?)?|実行\s+`?[^\s`]+`?))+)/;
const STEP_RE = /下書き(?:\s+`?([0-9a-f]{16})`?)?|実行\s+`?([^\s`]+)`?/g;

function parseNext(raw) {
  const n = raw.match(NEXT_RE);
  if (!n) return null;
  const drafts = [];
  let run = null;
  for (const m of n[1].matchAll(STEP_RE)) {
    if (m[0].startsWith('実行')) run = m[2];
    else drafts.push(m[1] || null);
  }
  // msgId は最初の下書きの返信先（下書きが1つだけの書き方との互換）
  return { kind: 'draft', msgId: drafts[0] ?? null, drafts, run };
}

// paths を渡せば、その台帳だけを読む（collect.mjs が設定の ledgers.roots で探した台帳）。path は root からの相対
export function collectWaiting(root = PROJECTS, paths = walkLedgers(root)) {
  const out = [];
  for (const p of [...paths].sort()) {
    readLines(p).forEach((raw, i) => {
      if (!/^\s*- \[[ !]\]\s+/.test(raw)) return;
      const m = raw.match(WAIT_RE);
      if (!m) return;
      const addrs = m[1].split(/[,、]/).map((a) => a.replace(/[`\s]/g, '')).filter(Boolean);
      out.push({
        path: relPath(root, p), line: i + 1, addrs, since: m[2], count: m[3] ? Number(m[3]) : 1,
        next: parseNext(raw),
        text: raw.replace(/^\s*- \[[ !]\]\s+/, '').trim(),
      });
    });
  }
  return out;
}
