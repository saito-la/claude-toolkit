/**
 * today.md の台帳の参照を読む・リンクに変える。check-today.mjs と linkify-today.mjs が共用する。
 *
 * 書き方は3つ。どれも同じ { path, n } の並びに読む（path は ~/Projects からの台帳のパス）。
 *   - 下書きの印：‹work/admin/action-items.md:70› ‹work/project-a/action-items.md:13,15,31-39›
 *     today.md を書くときはこれを置き、linkify-today.mjs が pj リンクに替える
 *   - pj リンク：[admin](pj://open?d=work/admin&p=…&s=…&f=action-items.md&l=70)。f は d からの相対
 *   - 以前の書き方：`work/admin/action-items.md` 70行・8・9行・64〜70行・（行 13, 15）・同 6行
 */
import { existsSync } from 'fs';
import { dirname } from 'path';
import { PROJECTS } from './lib/instance.mjs';

export { PROJECTS };

const expand = (list) => list.split(/[・、,]/).flatMap((part) => {
  const [a, b] = part.trim().split(/[〜-]/).map(Number);
  return Number.isFinite(a) ? Array.from({ length: (b || a) - a + 1 }, (_, i) => a + i) : [];
});

export function refsOf(text) {
  const out = [];
  for (const m of text.matchAll(/‹([^:›]+):([^›]+)›/g)) for (const n of expand(m[2])) out.push({ path: m[1], n, kind: 'mark' });
  for (const m of text.matchAll(/\(pj:\/\/open\?([^)\s]+)\)/g)) {
    const q = new URLSearchParams(m[1]);
    if (!q.get('f') || !q.get('l')) continue;
    for (const n of expand(q.get('l'))) out.push({ path: `${q.get('d')}/${q.get('f')}`, n, kind: 'link' });
  }
  const LIST = '\\d+(?:〜\\d+)?(?:\\s*[・、,]\\s*\\d+(?:〜\\d+)?)*';
  const re = new RegExp(`\`([^\`\\s]+\\.md)\`|行\\s*(${LIST})）|(?<![\\d/.〜:\\u3040-\\u30ff\\u4e00-\\u9fff])(${LIST})\\s*行`, 'g');
  let last = null;
  for (const m of text.replace(/\(pj:\/\/[^)]*\)|‹[^›]*›/g, '').matchAll(re)) {
    if (m[1]) { last = m[1]; continue; }
    if (!last) continue;
    for (const n of expand(m[2] || m[3])) out.push({ path: last, n, kind: 'text' });
  }
  return out;
}

// 台帳を持つプロジェクト＝台帳から上へたどって最初に CLAUDE.md があるディレクトリ
export function projectOf(path) {
  let dir = dirname(path);
  while (dir && dir !== '.') {
    if (existsSync(`${PROJECTS}/${dir}/CLAUDE.md`)) return dir;
    dir = dirname(dir);
  }
  return path.split('/')[0];
}
