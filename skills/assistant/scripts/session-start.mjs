#!/usr/bin/env node
/**
 * SessionStart フックの本体。collect.mjs を走らせ、要約をセッションの文脈へ渡す。
 *
 * インスタンスの .claude/settings.json の hooks.SessionStart から、インスタンスのフォルダで呼ぶ。
 * Windows でも動くよう bash と jq に頼らず node で書く。
 * 失敗してもフック自体は成功扱いにし、失敗の事実を文脈に載せる（黙って空になるのを防ぐ）。
 */
import { spawnSync } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { instance } from './lib/instance.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ENGINE = dirname(HERE);
const run = (script, args = []) => {
  const r = spawnSync(process.execPath, [join(HERE, script), ...args], { encoding: 'utf8' });
  return { status: r.status, out: `${r.stdout || ''}${r.stderr || ''}`.trim() };
};

const emit = (text) => console.log(JSON.stringify({ hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: text } }));

let inst;
try {
  inst = instance();
} catch (e) {
  emit(`秘書の仕組み: ${e.message}`);
  process.exit(0);
}
const { cfg, dir } = inst;
const { name } = cfg;
const doc = cfg.workDoc.name;
const S = `node ${join(ENGINE, 'scripts')}`;

// Apple Notes の捕捉用のノートは高速捕捉専用の入口（レビューは作業用の文書の1箇所に集約する。2026-08-01 決定）。
// collect.mjs が読む前に作業用の文書へ転記しておく。失敗しても collect.mjs は続行する（Notes は次回に残るだけ）
let notes = '';
if (cfg.features.appleNotes) {
  const r = run('notes-to-doc.mjs', ['--apply']);
  notes = r.status === 0 ? r.out : `notes-to-doc.mjs が失敗しました（終了コード ${r.status}）。Notes の内容はそのまま残っています。\n${r.out}`;
}

const c = run('collect.mjs');
const collected = c.status === 0
  ? c.out
  : `collect.mjs が失敗しました（終了コード ${c.status}）。認証切れなら scripts/auth-google.mjs で取り直す（README.md「資格情報」）。\n${c.out}`;

const steps = [
  `${name}: 朝の一式は「init」、日中の更新は「update」（仕様 ${join(ENGINE, 'docs', 'daily-and-update.md')}）。`,
  `日次ToDo の候補を収集しました（data/candidates.json を更新）。today.md を作る前に ${join(ENGINE, 'docs', 'design.md')}、このフォルダの overview.md（使い手固有の決まり。あれば）と data/candidates.json を読むこと。`,
  'エンジンのスクリプトはこのフォルダ（インスタンス）で実行する。エンジンのフォルダには何も書かない。',
  '期限候補は他の台帳と突合するまで「今日の期限」と断定しない。期限を過ぎた未完了の行（ledgers.overdue）は、突合のうえ「やること」の先頭に【期限超過】として出す。自分で決めた日付（目安。ledgers.aims）は期限として扱わず、優先順位の材料にだけ使う。「やること」は capacity に収まる量だけを見積付きで載せ、keepInMind はその直下の「意識しておく案件」に優先順で出す。',
  `載せる項目は前回の today.md を写さず、1件ずつ台帳の行・送信済みと予約送信（candidates.json の sent）・続報・${doc} の w で済んでいないかを確かめる（design.md「1件ずつの完了確認」）。`,
  'candidates.json の reminders は返事待ちが7日を超えた督促の候補で、下書きまで作る（design.md「督促の扱い」。送信しない）。',
  `済んでいると確かめた台帳の行は today.md に載せず、${S}/close-items.mjs --apply で閉じて退避し、閉じた行と根拠を報告する（design.md「書き込み方針」）。`,
  cfg.features.pjLinks
    ? `台帳の行は印 ‹<台帳>:<行>› で書き、${S}/linkify-today.mjs で pj リンクに替える（design.md「出力」）。`
    : '台帳の行は印 ‹<台帳>:<行>› で書く（design.md「出力」）。',
  `書いたら ${S}/check-today.mjs${cfg.features.checkNames ? ` と ${S}/check-names.mjs` : ''} を通し、終了コード1なら直してから見せる。`,
];
emit(`${steps.join('')}\n${notes ? `${notes}\n\n` : ''}${collected}`);
