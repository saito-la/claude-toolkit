/**
 * 受信メールの規則（インスタンスの mailAutopilot.rules が指す JSON）の照合。
 *
 * 規則は使い手が承認したものだけを書く。上から順に見て、最初に当たった規則を使う。
 *   { "name": "…", "label": "人が読む説明", "action": "archive" | "keep", "match": { "from": "…", "subject": "…", "body": "…", "any": [ {…}, … ] } }
 *   - archive：未読のままアーカイブへ移し、印「自動アーカイブ」を付ける
 *   - keep：判断に回さず、受信トレイに残して印「済」だけを付ける（判断に回すと迷いやすい形、判断の費用を省きたい形）
 * match の from・subject・body は正規表現（大文字と小文字を区別しない）で、書いたものすべてに当たれば一致する。
 * any は「どれか1つ」で、ほかの項目と AND で効く。全角の英数字で書かれた件名にも当たるよう、元の文字列と NFKC で
 * 正規化した文字列のどちらかに当たればよい。
 *
 * 正規表現の代わりに { "file": "…", "key": "…" } と書くと、その JSON（オブジェクトの配列）の各要素の key の正規表現の
 * どれかに当たれば一致する。ほかの道具が持つ一覧を写さずに読むためのもの。読めなければ当たらない扱いにする。
 */
import { readFileSync, existsSync } from 'fs';
import { expandHome } from '../lib/instance.mjs';

export const ACTIONS = ['archive', 'keep'];

/** 規則の一覧を読み、形を確かめる。ファイルが無ければ空 */
export function loadRules(file) {
  if (!file || !existsSync(file)) return [];
  const rules = JSON.parse(readFileSync(file, 'utf8'));
  if (!Array.isArray(rules)) throw new Error(`${file} は規則の配列でない`);
  rules.forEach((r, i) => {
    if (!ACTIONS.includes(r.action)) throw new Error(`${file} の ${i + 1} 番目（${r.name || '名前なし'}）の action が ${ACTIONS.join('・')} のどれでもない`);
    if (!r.match || typeof r.match !== 'object') throw new Error(`${file} の ${i + 1} 番目（${r.name || '名前なし'}）に match が無い`);
    for (const f of ['from', 'subject', 'body']) {
      const p = r.match[f];
      if (typeof p === 'string') new RegExp(p, 'i'); // 書き損じをここで落とす
    }
  });
  return rules;
}

const fileErrors = new Set();
function pattern(p) {
  if (typeof p === 'string') return p;
  try {
    const list = JSON.parse(readFileSync(expandHome(p.file), 'utf8'));
    const res = list.map((x) => x[p.key]).filter(Boolean);
    if (!res.length) throw new Error(`${p.key} を持つ要素が無い`);
    return res.map((r) => `(?:${r})`).join('|');
  } catch (e) {
    const msg = `規則の照合に使う ${p.file} を読めない：${e.message}`;
    if (!fileErrors.has(msg)) { fileErrors.add(msg); console.error(msg); }
    return null;
  }
}

function matches(m, msg) {
  if (!m) return true;
  for (const f of ['from', 'subject', 'body']) {
    if (!m[f]) continue;
    const src = pattern(m[f]);
    if (src === null) return false;
    const re = new RegExp(src, 'i');
    const v = msg[f] || '';
    if (!re.test(v) && !re.test(v.normalize('NFKC'))) return false;
  }
  if (m.any && !m.any.some((x) => matches(x, msg))) return false;
  return true;
}

/** msg は { from, subject, body }。当たった規則か null */
export const findRule = (rules, msg) => rules.find((r) => matches(r.match, msg)) || null;

/** 差出人の欄からアドレスだけを小文字で取る */
export const addressOf = (from) => (/<([^>]+)>/.exec(from || '')?.[1] || from || '').trim().toLowerCase();

/** 件名の形。返信・転送の印とメーリングリストの通し番号を落とし、数字の並びを # にする。規則の案を束ねるのに使う */
export function subjectShape(subject) {
  let s = String(subject || '').normalize('NFKC').trim();
  for (let i = 0; i < 10; i++) {
    const t = s.replace(/^\s*((re|fw|fwd|返信|転送)\s*[:：]|\[[^\]]*:\d+\])\s*/i, '');
    if (t === s) break;
    s = t;
  }
  return s.replace(/\d+/g, '#').replace(/\s+/g, ' ').trim();
}

const escapeRe = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** 差出人のアドレスと件名の形から、アーカイブの規則の案を作る（使い手が承認したら規則のファイルへ足す） */
export function proposeRule(from, subject) {
  const addr = addressOf(from);
  const shape = subjectShape(subject);
  return {
    name: `proposed-${addr.replace(/[^a-z0-9]+/g, '-').slice(0, 40)}`,
    label: `${addr} の「${shape}」`,
    action: 'archive',
    match: { from: `^(.*<)?${escapeRe(addr)}>?$`, subject: shape.split('#').map((p) => escapeRe(p).replace(/\s+/g, '\\s*')).join('\\d+') },
  };
}
