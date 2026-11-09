/**
 * インスタンスのフォルダを探し、設定 assistant.json を既定値と合わせて読む。正本。
 *
 * エンジン（このリポジトリ）はコードと手順書だけを持ち、自分のフォルダには何も書かない。
 * today.md・data/・設定・生活パターンなど、使い手ごとのものはすべてインスタンスのフォルダに置く。
 *
 * インスタンスのフォルダは次の順で決める。
 *   1. 環境変数 ASSISTANT_INSTANCE
 *   2. 作業ディレクトリから上へたどって最初に assistant.json があるフォルダ
 * どちらでも見つからなければ、名前を出して止まる。
 *
 * 設定の項目と既定値の正本は README.md「設定」。項目を足すときは、今の設定のままで動く既定値を
 * DEFAULTS に足す（使い手が設定を直さなくても改良が届くように）。既定値を置けない項目は need() で
 * 起動時に名前を出して止める。
 */
import { readFileSync, existsSync } from 'fs';
import { homedir } from 'os';
import { basename, dirname, join, resolve } from 'path';

// PowerShell では HOME が未定義のことがあるので os.homedir() を使う
export const HOME = homedir();
export const PROJECTS = join(HOME, 'Projects');
export const CONFIG_NAME = 'assistant.json';

export const DEFAULTS = {
  name: null, // インスタンスの呼び名。依頼文と開始時の文脈に使う。無ければフォルダの名前
  credentialDir: '~/.config/gdrive-mcp',
  oauthClient: 'credentials.json',
  workDoc: { name: '作業用の文書' },
  logDoc: { name: 'Log' },
  calendars: [],
  workplaceTitles: [], // 「(オフィス)」の接尾辞を持たない勤務場所の記載（event-kind.mjs）
  gmail: null,
  mailFiles: [],
  ledgers: { roots: ['.'], exclude: [] },
  features: { appleNotes: false, done: false, sessions: false, checkNames: false, pjLinks: false, workload: false },
  appleNotes: { title: 'Capture' },
  sessions: { command: null }, // features.sessions のときのセッション記録の集計の道具（ctx・gaps を持つもの）
  checkNames: { sources: [], wikiEntities: null },
  pjLinks: { link: null, open: null },
  attendanceNotices: [], // 勤務先への連絡の規則。各規則の既定値は notices.mjs の RULE_DEFAULTS
  holidays: { calendarId: 'ja.japanese#holiday@group.v.calendar.google.com', credential: null }, // credential が無ければ calendars の最初の Google の予定表のもの
  deadlineTasks: { credential: null, list: '締切' }, // 外部の期日を載せる Google Tasks。credential が無ければ使わない
  capacity: { dayMinutes: 1080, days: 2 }, // 「やること」の量の上限＝今日の残りの空き（dayMinutes まで）＋dayMinutes×(days-1)（docs/design.md「業務量」）
  prepPattern: '講義|講演|レクチャー|発表|セミナー|座長|演者|lecture',
  archiveScript: '~/.claude/scripts/archive-action-items.py',
};

export const expandHome = (p) => (typeof p === 'string' && (p === '~' || p.startsWith('~/')) ? join(HOME, p.slice(1)) : p);

function findDir() {
  if (process.env.ASSISTANT_INSTANCE) return resolve(expandHome(process.env.ASSISTANT_INSTANCE));
  for (let d = process.cwd(); ; d = dirname(d)) {
    if (existsSync(join(d, CONFIG_NAME))) return d;
    if (dirname(d) === d) return null;
  }
}

// 1段目の項目がオブジェクトなら既定値と重ね、それ以外は設定の値で置き換える
function merge(cfg) {
  const out = { ...DEFAULTS };
  for (const [k, v] of Object.entries(cfg)) {
    const d = DEFAULTS[k];
    out[k] = d && v && typeof d === 'object' && typeof v === 'object' && !Array.isArray(d) ? { ...d, ...v } : v;
  }
  return out;
}

let cached = null;

/** { dir, cfg, file(rel), cred(name) }。dir はインスタンスのフォルダの絶対パス */
export function instance() {
  if (cached) return cached;
  const dir = findDir();
  if (!dir) {
    throw new Error(`インスタンスのフォルダが見つからない。${CONFIG_NAME} のあるフォルダで実行するか、ASSISTANT_INSTANCE にそのフォルダを入れる`);
  }
  const cfg = merge(JSON.parse(readFileSync(join(dir, CONFIG_NAME), 'utf8')));
  cfg.name ??= basename(dir);
  cached = {
    dir,
    cfg,
    file: (...rel) => join(dir, ...rel),
    cred: (name) => join(expandHome(cfg.credentialDir), name),
  };
  return cached;
}

/** 既定値を置けない設定を取る。無ければ名前を出して止まる */
export function need(path) {
  const { dir, cfg } = instance();
  const v = path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), cfg);
  if (v === undefined || v === null || v === '') {
    throw new Error(`${join(dir, CONFIG_NAME)} に ${path} が無い（README.md「設定」）`);
  }
  return v;
}

/** 使わない設定の機能なら、そう言って終える。終了コード 0（手順を人ごとに分けないため、呼ばれても壊れない） */
export function requireFeature(name, what) {
  if (instance().cfg.features[name]) return;
  console.log(`この使い手の設定では ${what} を使わない（${CONFIG_NAME} の features.${name}）。何もせずに終える`);
  process.exit(0);
}
