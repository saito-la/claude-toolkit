#!/usr/bin/env node
/**
 * 受信メールの自動処理（mail-autopilot）。起動されるたびに、受信トレイの印の無い通を古い順に片付けて終わる（run-once）。
 * 定期の起動は端末の仕組み（Windows のタスクスケジューラ、macOS の launchd）に任せる。
 *
 *   node mail-autopilot.mjs                 試運転（既定）。判断だけを行い、メールに触らず、Chat にも送らない
 *   node mail-autopilot.mjs --live          本番。規則のアーカイブと印付けを行い、問いを Chat に送る（features.mailAutopilot が要る）
 *   node mail-autopilot.mjs --limit 5       1回に扱う通数（既定は設定の mailAutopilot.limit）
 *   node mail-autopilot.mjs --since-days 3  何日前の受信まで見るか（既定は設定の mailAutopilot.sinceDays）
 *
 * 1通の扱い：
 *   1. 規則（mailAutopilot.rules）に当たれば、その action を行う。archive は未読のままアーカイブへ移して印「自動アーカイブ」、
 *      keep は印「済」
 *   2. 当たらなければ Claude で判断する（scripts/mail/decide.mjs）。判断でメールは動かさない
 *      - 対応が要る：印「要対応」を付けて受信トレイに残す
 *      - 本人が読んで決める：印「済」
 *      - アーカイブしてよさそう：印「確認」を付けて残し、同じ形（差出人と件名の形）ごとに1回だけ、規則にしてよいかを Chat で問う
 *      - 迷う：印「確認」を付けて残し、Chat で問う
 *   3. 失敗した通は印を付けずに残し、次の起動でやり直す。同じ通が3回失敗したら、印「確認」を付けて Chat で知らせ、やり直さない
 *
 * 状態は印としてメールの側に持つ。インスタンスの data/mail-autopilot/ に置くのは、最後の実行の結果（last-run.json）、
 * 失敗の回数、問うた形、規則の案、試運転の記録（dry-run.csv。件名と差出人を含むので git に入れない）だけ。
 * 重なって起動されたら、後の方は何もせずに終わる（ロック）。設定の項目は README.md「設定」。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, unlinkSync, statSync } from 'fs';
import { join } from 'path';
import { instance, need, expandHome } from './lib/instance.mjs';
import { loadRules, findRule, addressOf, subjectShape, proposeRule } from './mail/rules.mjs';
import { decide, KINDS } from './mail/decide.mjs';
import { postChat, link } from './mail/chat.mjs';

const argv = process.argv.slice(2);
const LIVE = argv.includes('--live');
const opt = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };

const { dir, cfg, file } = instance();
const MA = cfg.mailAutopilot;
const MARKS = { ...DEFAULT_MARKS(), ...(MA.marks || {}) };
const LIMIT = Number(opt('--limit') ?? MA.limit);
const SINCE = Number(opt('--since-days') ?? MA.sinceDays);
const DATA = file('data', 'mail-autopilot');
mkdirSync(DATA, { recursive: true });

function DEFAULT_MARKS() { return { done: '秘書/済', archived: '秘書/自動アーカイブ', attention: '秘書/要対応', ask: '秘書/確認' }; }

if (LIVE && !cfg.features.mailAutopilot) {
  console.log('この使い手の設定では mail-autopilot の本番を使わない（assistant.json の features.mailAutopilot）。試運転は --live を付けずに回す');
  process.exit(0);
}

// ---------------------------------------------------------------- 小道具

const readJson = (f, d) => { try { return JSON.parse(readFileSync(f, 'utf8')); } catch { return d; } };
const writeJson = (f, v) => writeFileSync(f, JSON.stringify(v, null, 2));
const nowIso = () => new Date().toISOString();
const csvCell = (v) => { const s = v == null ? '' : String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

const LOCK = join(DATA, 'lock');
function takeLock() {
  if (existsSync(LOCK)) {
    const age = Date.now() - statSync(LOCK).mtimeMs;
    if (age < 30 * 60_000) return false; // 30分より新しいロックは、ほかの起動が動いている
  }
  writeFileSync(LOCK, `${process.pid} ${nowIso()}`);
  return true;
}
const dropLock = () => { try { unlinkSync(LOCK); } catch { /* 無ければよい */ } };

async function openMail() {
  const m = need('mailAutopilot.mail');
  if (m.type === 'gmail') {
    const { openGmail } = await import('./mail/gmail.mjs');
    return openGmail({ credential: need('mailAutopilot.mail.credential'), account: m.account, marks: MARKS });
  }
  if (m.type === 'outlook') {
    const { openOutlook } = await import('./mail/outlook.mjs');
    return openOutlook({ mailctl: expandHome(m.mailctl), marks: MARKS });
  }
  throw new Error(`mailAutopilot.mail.type が gmail・outlook のどちらでもない：${m.type}`);
}

// ---------------------------------------------------------------- 本体

if (!takeLock()) {
  console.log('ほかの起動が動いているので何もせずに終える（data/mail-autopilot/lock）');
  process.exit(0);
}

const summary = { at: nowIso(), mode: LIVE ? 'live' : 'dry-run', seen: 0, rule: 0, judged: 0, failed: 0, kinds: {}, asked: 0, chatErrors: 0 };
try {
  await run();
} catch (e) {
  summary.error = e.message;
  console.error(`止まった：${e.message}`);
  if (LIVE && MA.chat?.webhookEnv) await postChat(MA.chat.webhookEnv, `mail-autopilot が止まった：${e.message.slice(0, 200)}`).catch(() => {});
  process.exitCode = 1;
} finally {
  writeJson(join(DATA, 'last-run.json'), summary);
  dropLock();
}

async function run() {
  const mail = await openMail();
  const rules = loadRules(file(MA.rules));
  const self = await mail.self();
  const failures = readJson(join(DATA, 'failures.json'), {});
  const asked = readJson(join(DATA, 'asked-shapes.json'), {});
  const seenFile = join(DATA, 'dry-run-seen.json');
  const seen = LIVE ? {} : readJson(seenFile, {}); // 試運転は印を付けないので、判断した通をここで覚えて二度判断しない
  const csv = join(DATA, 'dry-run.csv');
  if (!LIVE && !existsSync(csv)) writeFileSync(csv, '﻿判断日時,受信,差出人,件名,規則,扱い,迷い,問い,一言,理由,機微,期限,秒\n');

  const all = await mail.listUnmarked({ sinceDays: SINCE, limit: LIMIT + Object.keys(seen).length });
  const list = all.filter((m) => !seen[m.id]).slice(0, LIMIT);
  console.log(`${LIVE ? '本番' : '試運転'}：受信トレイの印の無い通 ${all.length} 件のうち ${list.length} 件を扱う（${SINCE} 日以内）`);

  const candidates = new Map(); // アーカイブしてよさそうな形 → { rule, count, example }
  const say = async (text) => {
    if (!LIVE) { console.log(`  [Chat に送る文面] ${text}`); return; }
    if (!MA.chat?.webhookEnv) return;
    try { await postChat(MA.chat.webhookEnv, text); } catch (e) { summary.chatErrors++; console.error(e.message); }
  };
  const mark = async (id, name) => { if (LIVE) await mail.mark(id, name); };
  const show = (r, msg) => (MA.chat?.hideSensitive !== false && r.sensitive ? '人事・研究対象者の件（要点は省略）' : r.line || msg.subject);

  for (const head of list) {
    summary.seen++;
    const row = { 規則: '', 扱い: '', 迷い: '', 問い: '', 一言: '', 理由: '', 機微: '', 期限: '', 秒: '' };
    try {
      const msg = await mail.read(head.id);
      const rule = findRule(rules, msg);
      if (rule) {
        summary.rule++;
        row.規則 = rule.name || rule.label || '';
        row.扱い = rule.action === 'archive' ? '規則でアーカイブ' : '規則で残す';
        if (LIVE && rule.action === 'archive') { await mail.archive(msg.id); await mail.mark(msg.id, MARKS.archived); }
        else await mark(msg.id, MARKS.done);
      } else {
        const { value: r, seconds } = await decide({ msg, self, owner: MA.owner, guidanceFile: MA.guidance ? file(MA.guidance) : null, model: MA.model, cwd: dir });
        summary.judged++;
        summary.kinds[r.kind] = (summary.kinds[r.kind] || 0) + 1;
        Object.assign(row, { 扱い: KINDS[r.kind], 迷い: r.unsure ? '迷い' : '', 問い: r.question, 一言: r.line, 理由: r.reason, 機微: r.sensitive ? '機微' : '', 期限: r.deadline || '', 秒: seconds });
        const what = link(mail.link(msg.id), show(r, msg));
        if (r.unsure) {
          await mark(msg.id, MARKS.ask);
          await say(`迷い：${what}／傾いた扱い：${KINDS[r.kind]}／問い：${r.sensitive && MA.chat?.hideSensitive !== false ? '（本文を見て扱いを返信で）' : r.question}　受信トレイに残してある`);
          summary.asked++;
        } else if (r.kind === 'archive') {
          await mark(msg.id, MARKS.ask);
          const key = `${addressOf(msg.from)}\t${subjectShape(msg.subject)}`;
          const c = candidates.get(key) || { rule: proposeRule(msg.from, msg.subject), count: 0, example: what };
          c.count++;
          candidates.set(key, c);
        } else if (r.kind === 'attention') {
          await mark(msg.id, MARKS.attention);
        } else {
          await mark(msg.id, MARKS.done);
        }
      }
      delete failures[head.id];
    } catch (e) {
      summary.failed++;
      row.扱い = '失敗';
      row.理由 = e.message.slice(0, 200);
      const n = (failures[head.id] = (failures[head.id] || 0) + 1);
      console.error(`失敗（${n}回目）：${head.subject} — ${e.message}`);
      if (n >= 3) {
        await mark(head.id, MARKS.ask).catch(() => {});
        await say(`失敗：${link(mail.link(head.id), head.subject)}　3回続けて処理できなかったので受信トレイに残し、やり直さない。${e.message.slice(0, 100)}`);
        delete failures[head.id];
      }
    }
    if (!LIVE) {
      seen[head.id] = nowIso();
      appendFileSync(csv, [nowIso(), head.received, head.from, head.subject, row.規則, row.扱い, row.迷い, row.問い, row.一言, row.理由, row.機微, row.期限, row.秒].map(csvCell).join(',') + '\n');
    }
    console.log(`  ${head.received.slice(0, 16)} ${row.扱い}${row.迷い ? '（迷い）' : ''} ${head.subject.slice(0, 50)}`);
  }

  // アーカイブしてよさそうな形を、形ごとに1回だけ問う。案は data/mail-autopilot/rule-proposals.json に溜め、承認されたら規則へ移す
  const proposals = readJson(join(DATA, 'rule-proposals.json'), []);
  for (const [key, c] of candidates) {
    if (asked[key]) { asked[key].count += c.count; continue; }
    await say(`アーカイブの規則にしてよいか：${c.rule.label}（今回 ${c.count} 通。例：${c.example}）　同じ形のメールは受信トレイに残してある`);
    asked[key] = { at: nowIso(), count: c.count };
    proposals.push({ key, at: nowIso(), ...c.rule });
    summary.asked++;
  }
  if (LIVE) { writeJson(join(DATA, 'asked-shapes.json'), asked); writeJson(join(DATA, 'rule-proposals.json'), proposals); }
  else writeJson(seenFile, seen);
  writeJson(join(DATA, 'failures.json'), failures);
  console.log(`終わり：${summary.seen} 件（規則 ${summary.rule}・判断 ${summary.judged}・失敗 ${summary.failed}）。扱いの内訳 ${JSON.stringify(summary.kinds)}${LIVE ? '' : `。記録は ${csv}`}`);
}
