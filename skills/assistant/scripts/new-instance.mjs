#!/usr/bin/env node
/**
 * 新しい使い手のインスタンスの骨組みを書く。手順は README.md「3.3. インスタンス」。
 *
 *   node ~/.claude/skills/assistant/scripts/new-instance.mjs <インスタンスのフォルダ> --account <Google のアドレス> [--credential <資格情報のファイル名>] [--dry-run]
 *
 * 書くもの（すでにあるファイルは上書きせず、飛ばしたと出す）：
 *   assistant.json          設定。workDoc.id・logDoc.id は空で書く。使い手が文書を作って埋めるまで、使うスクリプトは項目の名前を出して止まる
 *   CLAUDE.md               インスタンスの説明。pj の索引と Orca の一覧に載るために要る
 *   .gitignore              data/ を外す。today.md は入れる。Orca などの git の worktree で開くと git に入ったファイルしか無く、
 *                           入れないと作業場ごとに前回の today.md が消えるため。見せられない件名が混ざるなら使い手が外す
 *   .claude/settings.json   開始時の収集のフック
 *
 * 開始時のフックの1行の正本はこのファイルの HOOK。README.md「2.2」はここを指す。
 * 資格情報は1つのファイル（既定 .google-work.json）に documents・calendar.readonly・gmail.readonly を持たせる前提（README.md「3.2」）。
 * Windows でも動くよう、パスは path.join で組み、ホームは os.homedir() で取る。
 */
import { existsSync, mkdirSync, writeFileSync } from 'fs';
import { dirname, join, resolve } from 'path';
import { homedir } from 'os';

const argv = process.argv.slice(2);
const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv.splice(i, 2)[1]; };
const DRY = argv.includes('--dry-run');
if (DRY) argv.splice(argv.indexOf('--dry-run'), 1);
const account = opt('--account');
const credential = opt('--credential') || '.google-work.json';
const [target] = argv;

if (!target || !account) {
  console.error('usage: node new-instance.mjs <インスタンスのフォルダ> --account <Google のアドレス> [--credential <資格情報のファイル名>] [--dry-run]');
  process.exit(2);
}
const dir = resolve(target === '~' || target.startsWith('~/') ? join(homedir(), target.slice(1)) : target);

// Claude Code は Windows でもフックを Git Bash で動かすので、Mac と同じ sh の書き方の1行で足りる（README.md「5. 動作環境」）
const HOOK = 'PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"; f="$HOME/.claude/skills/assistant/scripts/session-start.mjs"; if [ -f "$f" ]; then node "$f"; else echo "秘書の仕組みのエンジンがこの端末に無い。claude-toolkit の install.py を流してから開き直す"; fi';

const files = {
  'assistant.json': JSON.stringify({
    name: 'planner',
    workDoc: { name: 'ToDo', id: '', credential },
    logDoc: { name: 'Log', id: '', credential },
    calendars: [{ label: '予定表', credential }],
    gmail: { account, credential, sentExcludeSubjects: [] },
    features: {},
  }, null, 2) + '\n',
  'CLAUDE.md': `# planner

秘書の仕組みのインスタンス。各プロジェクトの台帳（\`action-items.md\`）・作業用の文書・予定表・Gmail を読み、その日にやることを並べた today.md を作る。

- 朝は「init」、日中は「update」と言う
- 仕組みの決まりと手順の正本はエンジン（\`~/.claude/skills/assistant\`）の \`README.md\`・\`docs/design.md\`・\`docs/daily-and-update.md\`。ここには写さない
- 設定は \`assistant.json\`。この使い手に固有の決まりがあれば \`overview.md\` に書く
`,
  '.gitignore': '# collect.mjs の出力。毎回作り直せる\ndata/\n',
  '.claude/settings.json': JSON.stringify({
    hooks: { SessionStart: [{ hooks: [{ type: 'command', command: HOOK, timeout: 90 }] }] },
  }, null, 2) + '\n',
};

for (const [rel, body] of Object.entries(files)) {
  const p = join(dir, ...rel.split('/'));
  if (existsSync(p)) { console.log(`・飛ばした（すでにある）：${rel}`); continue; }
  if (!DRY) {
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, body);
  }
  console.log(`${DRY ? '・書く予定' : '✅ 書いた'}：${rel}`);
}
console.log('\n次：作業用の文書と記録用の文書を作り、assistant.json の workDoc.id・logDoc.id に入れる（README.md「3.3. インスタンス」）');
