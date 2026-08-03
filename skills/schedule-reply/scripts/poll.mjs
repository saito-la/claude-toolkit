#!/usr/bin/env node
// AI秘書: 日程調整メール自動回答（tonton / 調整さん）— シングルユーザー向け
//
// 1) Gmail inbox を検索（in:inbox + tonton/調整さん URL）
// 2) 入力依頼か判定・除外（config.exclude で設定した送信者・件名パターン）
// 3) 回答ページを開き候補日時を取得
// 4) Googleカレンダーの空きを平日8-17時で判定（◯/△/✕）
// 5) config.displayName の名義で入力・送信（chousei は全自動 / tonton は補助）
// 6) 変更可能URLをブラウザで開く
// 7) 処理済みメールを "Scheduling" へ移動・既読化
//
// Usage: node poll.mjs [--dry-run] [--verbose] [--config <path>] [--only <GmailメッセージID>]
// --only は inbox 内の該当1通のみを処理する（会話経由の単発依頼向け。省略時は従来通り未処理分を全件処理）。
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { makeClient, makeGmail, makeCalendar, parseMessage } from './lib/google.mjs';
import { classify } from './lib/classify.mjs';
import { judge, boundingWindow } from './lib/availability.mjs';
import { withBrowser, getAdapter, openForReview } from './lib/browser.mjs';
import { printSummary, candidateLabel, makeFileLogger } from './lib/logger.mjs';
import { loadProcessed, markProcessed } from './lib/state.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const a = { dryRun: false, send: false, verbose: false, config: join(HERE, 'config.json'), only: null };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--dry-run') a.dryRun = true;
    else if (argv[i] === '--send') a.send = true;
    else if (argv[i] === '--verbose') a.verbose = true;
    else if (argv[i] === '--config') a.config = resolve(argv[++i]);
    else if (argv[i] === '--only') a.only = argv[++i];
  }
  return a;
}

async function main() {
  const args = parseArgs(process.argv);
  if (!existsSync(args.config)) {
    console.error(`config not found: ${args.config}\n→ cp config.example.json config.json し、パスを確認してください。`);
    process.exit(1);
  }
  const cfg = JSON.parse(readFileSync(args.config, 'utf8'));
  // --dry-run forces judge-only; --send forces live; otherwise config decides (safe default).
  const dryRun = args.dryRun ? true : args.send ? false : (cfg.flags?.dryRun ?? true);
  const statePath = join(HERE, 'state.jsonl');
  const flog = makeFileLogger(join(HERE, 'logs', 'scheduler.log'));

  // --- Google clients ---
  const gClient = makeClient({ credentialsPath: cfg.gmail.credentialsPath, oauthClientPath: cfg.gmail.oauthClientPath });
  const gmail = makeGmail(gClient);
  const calClient = makeClient({ credentialsPath: cfg.calendar.credentialsPath, oauthClientPath: cfg.calendar.oauthClientPath });
  const calendar = makeCalendar(calClient);

  const canRelabel = gClient.hasScope('https://www.googleapis.com/auth/gmail.modify');
  if (cfg.flags?.relabel && !canRelabel) {
    console.error('⚠ gmail.modify スコープ未付与のため relabel/既読化はスキップします（README「Gmailスコープ拡張」参照）。');
  }

  const processed = loadProcessed(statePath);
  const skips = [];

  // --- 1) search inbox ---
  const allMsgs = await gmail.searchMessages(cfg.gmail.searchQuery, 25);
  const msgs = args.only ? allMsgs.filter((m) => m.id === args.only) : allMsgs;
  if (args.verbose) {
    console.error(`inbox hits: ${allMsgs.length}`);
    if (args.only) console.error(`--only ${args.only} で絞り込み: ${msgs.length}件`);
  }
  if (args.only && !msgs.length) {
    console.error(`--only で指定したメッセージがinbox検索結果にありません（既読/処理済み/検索クエリ対象外の可能性）: ${args.only}`);
  }

  let handled = 0;
  for (const { id } of msgs) {
    if (processed.has(id)) continue;
    const mail = parseMessage(await gmail.getMessage(id));
    const c = classify(mail, cfg);

    if (c.excluded) {
      skips.push(c.excludeReason);
      flog({ event: 'skip', id, subject: mail.subject, reason: c.excludeReason });
      if (!dryRun) markProcessed(statePath, id, { skipped: c.excludeReason });
      continue;
    }
    if (!c.matched) {
      if (args.verbose) console.error(`skip (${mail.subject}): ${c.reason}`);
      flog({ event: 'skip', id, subject: mail.subject, reason: c.reason });
      if (!dryRun) markProcessed(statePath, id, { skipped: c.reason });
      continue;
    }

    // --- 3-6) process one poll ---
    try {
      const adapter = getAdapter(c.tool);
      const result = await withBrowser(cfg.browser?.headless ?? true, async (page) => {
        const { title, candidates } = await adapter.scrape(page, c.url);
        if (!candidates.length) return { title, candidates: [], empty: true };

        // --- 4) availability ---
        const { timeMin, timeMax } = boundingWindow(candidates);
        const busy = await calendar.freeBusy({
          calendarIds: cfg.calendar.calendarIds, timeMin, timeMax, timezone: cfg.calendar.timezone,
        });
        const decisions = candidates.map((cand) => ({
          candidate: cand,
          ...judge(cand, busy, cfg, { supportsTriangle: adapter.supportsTriangle }),
        }));

        // --- 5) answer ---
        const ans = await adapter.answer(page, c.url, {
          displayName: cfg.displayName,
          decisions,
          dryRun,
          editPassword: cfg.browser?.tontonEditPassword || '',
          busy,
          cfg,
        });
        return { title, candidates, decisions, ans };
      });

      // Poll closed / stale link / not an answer page → soft-skip (don't retry forever).
      if (result.empty) {
        const reason = '候補日時が取得できず（掲示終了・締切・リンク切れ等）→スキップ';
        if (args.verbose) console.error(`skip (${mail.subject}): ${reason}`);
        flog({ event: 'skip', id, subject: mail.subject, tool: c.tool, reason });
        if (!dryRun) markProcessed(statePath, id, { skipped: reason });
        continue;
      }

      // --- 6) open editable URL for review ---
      let reviewOpened = false;
      if (!dryRun && cfg.browser?.openForReviewAfterSubmit && result.ans.reviewUrl) {
        reviewOpened = await openForReview(result.ans.reviewUrl);
      }

      // --- 7) relabel + mark read ---
      let relabeled = false;
      if (!dryRun && cfg.flags?.relabel && canRelabel) {
        const labelId = await gmail.ensureLabel(cfg.gmail.schedulingLabel);
        const remove = ['INBOX'];
        if (cfg.flags?.markRead) remove.push('UNREAD');
        await gmail.modifyMessage(id, { addLabelIds: [labelId], removeLabelIds: remove });
        relabeled = true;
      }

      // --- summary ---
      // Prefer adapter-provided per-date marks (tonton derives them from the slots it
      // actually paints), so the log matches what was submitted; else use day judge.
      const dayMarks = result.ans.dayMarks || {};
      const rows = result.decisions.map((d) => {
        const dm = dayMarks[d.candidate.date];
        return { label: candidateLabel(d.candidate), mark: dm?.mark ?? d.mark, reason: dm?.reason ?? d.reason };
      });
      const outcome = [];
      if (dryRun) {
        outcome.push('DRY-RUN: 送信・ラベル移動・URLオープンは行っていません（判定のみ）。');
      } else {
        if (result.ans.submitted) outcome.push('送信完了。' + (reviewOpened ? '変更可能URLをブラウザで開きました。内容をご確認ください。' : ''));
        else if (result.ans.assisted) outcome.push(result.ans.note + (reviewOpened ? '（ブラウザを開きました）' : ''));
        outcome.push(relabeled ? `メールを "${cfg.gmail.schedulingLabel}" ラベルへ移動し既読化しました。`
                               : 'ラベル移動はスキップ（scope未付与 or 無効）。');
      }
      const block = printSummary({ tool: c.tool, subject: mail.subject, rows, outcome });
      flog({ event: 'processed', id, tool: c.tool, subject: mail.subject, dryRun,
        submitted: result.ans.submitted, relabeled, rows });

      if (!dryRun) markProcessed(statePath, id, { tool: c.tool, submitted: result.ans.submitted });
      handled++;
    } catch (e) {
      console.error(`ERROR processing ${mail.subject}: ${e.message}`);
      flog({ event: 'error', id, subject: mail.subject, error: e.message });
      // do not mark processed on error → retry next run
    }
  }

  if (skips.length) {
    process.stdout.write(`\n（スキップ: ${skips.join(' / ')}）\n`);
  }
  if (!handled && !skips.length && args.verbose) console.error('処理対象なし。');
}

main().catch((e) => { console.error(e); process.exit(1); });
