/**
 * ledgers.mjs — 全プロジェクトの台帳（action-items.md）を探し、返事待ちの行を読む
 *
 * collect.mjs と インスタンスの外の道具（受信メールの自動処理など）の両方が使う。
 * 外の道具はインスタンスの外から import するので、ここは設定（assistant.json）を読まない。
 * 返事待ちの行の書式の正本は claude-toolkit の conventions/action-items-convention.md「返事待ちの行」で、
 * 機械で読む側の正本はこのファイル。読む側を2か所に持たないため、外の道具はここを import する。
 */
import { readFileSync, readdirSync } from 'fs';
import { homedir } from 'os';
import { join, relative } from 'path';

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
    readFileSync(p, 'utf8').split('\n').forEach((raw, i) => {
      if (!/^\s*- \[[ !]\]\s+/.test(raw)) return;
      const m = raw.match(WAIT_RE);
      if (!m) return;
      const addrs = m[1].split(/[,、]/).map((a) => a.replace(/[`\s]/g, '')).filter(Boolean);
      out.push({
        path: relative(root, p), line: i + 1, addrs, since: m[2], count: m[3] ? Number(m[3]) : 1,
        next: parseNext(raw),
        text: raw.replace(/^\s*- \[[ !]\]\s+/, '').trim(),
      });
    });
  }
  return out;
}
