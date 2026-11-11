#!/usr/bin/env node
/**
 * collect.mjs — 日次ToDo の候補を全入力源から収集して、インスタンスの data/candidates.json に出す
 *
 * 設計・ゾーン定義の正本は ../docs/design.md。読み取り専用（作業用の文書にも台帳にも書き込まない）。
 * 優先度はここでは計算しない。合成層（セッション中の判断）が candidates.json を読んで today.md を書く。
 *
 * 入力源（どれを読むかと資格情報はインスタンスの assistant.json。README.md「設定」）：
 *   1. 作業用の文書の捕捉ゾーン    … 見出し1「# Capture」直下〜次の見出し1
 *   2. 作業用の文書の直近ToDoゾーン … 「# 2w Schedule」の当日の日付見出し直下〜見出し1「# Sink」
 *   3. Apple Notes の捕捉用のノート（features.appleNotes）
 *   4. 台帳（action-items.md と <topic>_action_items.md）の未処理行。最後に変えてから（git の最後の commit。手元に変更があれば mtime）--ledger-days 日を過ぎた台帳は、
 *      期日（目安は除く）・「待ち：」・[!] の行を持つときだけ読む（止まっている台帳ほど見落とすため。stale・staleDays を付ける）
 *   5. 予定表（calendars）の当日分と先の分（空き時間・場所・直前直後の面会相手・予定の重なり）。複数あれば1つの並びにまとめる
 *   6. インスタンスの top-priority.md（使い手が指定した最優先課題。`## ` 節ごとに構造化する）
 *   7. Gmail の inbox（`in:inbox`。件名・差出人・snippet・リンク）
 *   8. Gmail の直近7日の送信済みと予約送信（1件ずつの完了確認の材料。docs/design.md）
 *   9. ファイルのメール（設定の mailFiles。lib/mail-files.mjs）の受信トレイと送信済み
 *  11. 台帳の未処理行が引く Gmail の ID のうち、受信トレイに残っていて使い手宛に届いて、その後同じスレッドで使い手が送っていないもの
 *      （ledgers.ballReturned。docs/design.md「1件ずつの完了確認」）
 *  10. 勤務先への連絡（設定の attendanceNotices。判定の正本は notices.mjs）：決まった曜日に勤務先へ行けない日と、
 *      その連絡が送信済みのメールかインスタンスの attendance-notices.csv にあるか
 *
 * Usage:
 *   node collect.mjs                    # 収集して data/candidates.json に書く
 *   node collect.mjs --stdout           # 書かずに標準出力へ
 *   node collect.mjs --ledger-days 60   # 台帳の鮮度しきい値を変える（既定 30）
 *   node collect.mjs --no-notes         # Apple Notes を見ない
 *   node collect.mjs --no-gmail         # Gmail inbox を見ない
 *
 * 認証は設定の credentialDir にある資格情報（workDoc.credential・calendars[].credential・gmail.credential）。
 * type が "file" の予定表は認証せずにファイルを読む（lib/calendars.mjs）。
 * 切れた場合は scripts/auth-google.mjs で取り直す（README.md「資格情報」）。
 */

import { readFileSync, writeFileSync, statSync, mkdirSync, existsSync } from 'fs';
import { execFileSync } from 'child_process';
import { dirname, join, relative, sep } from 'path';
import { classifyEventTitle } from './event-kind.mjs';
import { conflictChecker, loadRules } from './conflicts.mjs';
import { walkLedgers, collectWaiting, readLines, lastChanged } from './ledgers.mjs';
import { instance, need, PROJECTS } from './lib/instance.mjs';
import { accessToken, getDoc, paragraphs } from './lib/google.mjs';
import { fileEvents, isFileCalendar } from './lib/calendars.mjs';
import { ymdOfDateHeading as ymdOfHeading } from './lib/date-heading.mjs';
import { readMailFile } from './lib/mail-files.mjs';
import { fetchHolidays } from './lib/holidays.mjs';
import { judgeRule, loadMarks, MARKS_FILE, withRuleDefaults } from './notices.mjs';
import { header, myAddresses, kindOf } from './lib/gmail-kind.mjs';

// 台帳のパスは today.md の参照と pj リンクに使うので、Windows でも / で区切る
const relPath = (from, to) => relative(from, to).split(sep).join('/');

// ── 設定 ───────────────────────────────────────────────
const INST = instance();
const CFG = INST.cfg;
const DOC_ID = need('workDoc.id');
const DOC_CRED = need('workDoc.credential');
const DOC = CFG.workDoc.name; // 作業用の文書の呼び名。メッセージに使う
const SCHEDULE_HEADING = '# 2w Schedule';
const CAPTURE_HEADING = '# Capture';
const SINK_HEADING = '# Sink';
const NOTES_TITLE = CFG.appleNotes.title;
const TZ = 'Asia/Tokyo';
const CALENDARS = CFG.calendars;
const CONFLICT_RULES = loadRules(INST.file('conflict-rules.json'));
// # Sink が未設置のときに読む上限行数（安全側に倒すためのフォールバック）
const RECENT_FALLBACK_LINES = 300;
// 「# 見出し」の文脈を何ブロック先まで引き継ぐか
const CONTEXT_REACH = 3;
const MIN_GAP_MIN = 15;
// カレンダーに入っているが「予定」ではない枠（自衛ブロック・勤務場所の記載）は
// 可処分時間としては空きとして扱い、ラベルだけ添えて場所の手がかりにする。
// 判定の正本は event-kind.mjs（doc-2w.mjs と共用）。

// ── 引数 ───────────────────────────────────────────────
const argv = process.argv.slice(2);
const STDOUT = argv.includes('--stdout');
const NO_NOTES = argv.includes('--no-notes') || !CFG.features.appleNotes;
const NO_GMAIL = argv.includes('--no-gmail') || !CFG.gmail;
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i !== -1 && argv[i + 1] ? argv[i + 1] : fallback;
};
const LEDGER_DAYS = parseInt(arg('--ledger-days', '30'), 10);
// 明日以降を何日先までカレンダーで見るか。講義の準備（2週間前に着手）が見える長さにする
const UPCOMING_DAYS = parseInt(arg('--upcoming-days', '21'), 10);
// 使い手が話す側に立つかもしれない予定の目印（設定の prepPattern）。題名だけで付く候補で、話す役かは today.md を書く側が案内の本文で確かめる。準備の行が台帳に無いまま当日を迎えるのを防ぐ
const PREP_PATTERN = new RegExp(CFG.prepPattern, 'i');

const OUT_PATH = INST.file('data', 'candidates.json');
const warnings = [];
const warn = (m) => { warnings.push(m); console.error(`⚠️  ${m}`); };

// ── 日付ユーティリティ（すべて JST 前提）─────────────────
function todayJst() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}
function addDays(ymd, n) {
  const d = new Date(`${ymd}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function hhmm(iso) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}
// JST の壁時計での現在時刻 "HH:MM"
function nowHhmm() {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date());
}
const minOf = (t) => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };
const timeOf = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;

// ── 作業用の文書 ────────────────────────────────────────
async function fetchWorkDoc() {
  return getDoc(DOC_ID, await accessToken(DOC_CRED));
}

// 先頭タブの本文段落を平坦化。startIndex は削除の実装で行を指すのに使う
const paragraphsOf = (doc) => paragraphs(doc).map(({ start, end, heading, h1, text }) => ({ start, end, heading, h1, text }));

const MARKER_RE = /^(w|A|Y|K|H)(?=\s|$)/;
const URL_RE = /https?:\/\/[^\s]+/g;

/**
 * 段落列 → ブロック配列。空行1つ以上で区切る。
 * 連続する非空行が1項目か複数項目かは機械判定できない（実際の文書には両方の書き方が混在する）。
 * ここでは lines を保持したまま渡し、分割の判断は合成層に委ねる。
 */
function toBlocks(paras, offset) {
  const blocks = [];
  let cur = null;
  paras.forEach((p, i) => {
    const t = p.text.trim();
    if (!t) { cur = null; return; }
    if (!cur) {
      cur = { order: blocks.length + 1, docIndex: p.start, zoneLine: offset + i + 1, lines: [], markers: [], urls: [], atHome: false };
      blocks.push(cur);
    }
    const m = t.match(MARKER_RE);
    if (m && !cur.markers.includes(m[1])) cur.markers.push(m[1]);
    if (t.includes('@home')) cur.atHome = true;
    for (const u of t.match(URL_RE) || []) if (!cur.urls.includes(u)) cur.urls.push(u);
    cur.lines.push({ docIndex: p.start, text: t });
  });
  return blocks;
}

// 見出し1のインデックスを完全一致で探す
const findH1 = (paras, label) => paras.findIndex((p) => p.h1 && p.text.trim() === label);

function captureZone(paras) {
  const ai = findH1(paras, CAPTURE_HEADING);
  if (ai === -1) {
    warn(`${DOC} に見出し1「${CAPTURE_HEADING}」が無いため捕捉ゾーンを読めません。action-items.md「出口の設置」参照`);
    return { found: false, blocks: [] };
  }
  let j = ai + 1;
  while (j < paras.length && !paras[j].h1) j++;
  return { found: true, blocks: toBlocks(paras.slice(ai + 1, j), ai + 1) };
}

/**
 * 直近ToDoゾーン：「# 2w Schedule」配下の当日の日付見出し直下から「# Sink」までを読む。
 * Sink が無ければ既定行数で打ち切る。
 *
 * 日付見出しは新しい日付が上に積まれる降順で並ぶ。当日を「文書順で最後の日付見出し」で
 * 決めると最古の見出しを指し、当日書いた行が丸ごと落ちる（2026-08-23 に発覚）。
 * 日付の一致で探し、当日の見出しがまだ無い日は当日以前で最も新しい見出しへ落とす。
 */
function recentZone(paras, today) {
  const ai = findH1(paras, SCHEDULE_HEADING);
  if (ai === -1) throw new Error(`${DOC} に見出し1「${SCHEDULE_HEADING}」が見つかりません`);

  const dates = [];
  for (let i = ai + 1; i < paras.length; i++) {
    if (!paras[i].h1) continue;
    const ymd = ymdOfHeading(paras[i].text);
    if (!ymd) break; // 日付でない見出し1でセクション終了
    dates.push({ index: i, ymd, text: paras[i].text.trim() });
  }
  if (!dates.length) throw new Error(`「${SCHEDULE_HEADING}」配下に日付見出しがありません`);

  // 当日以前で最も新しい見出し。1つも無ければ（全て未来日）最古の見出しへ落とす。
  const past = dates.filter((d) => d.ymd <= today).sort((a, b) => b.ymd.localeCompare(a.ymd));
  let start = past[0];
  if (!start) {
    start = dates[dates.length - 1];
    warn(`「${SCHEDULE_HEADING}」配下に ${today} 以前の日付見出しが無いため「${start.text}」直下から読みました`);
  } else if (start.ymd !== today) {
    warn(`${DOC} に ${today} の日付見出しが無いため直近の「${start.text}」直下から読みました`);
  }

  const si = findH1(paras, SINK_HEADING);
  let endExclusive;
  let sinkFound = si !== -1 && si > start.index;
  if (sinkFound) {
    endExclusive = si;
  } else {
    endExclusive = Math.min(paras.length, start.index + 1 + RECENT_FALLBACK_LINES);
    warn(`${DOC} に見出し1「${SINK_HEADING}」が無いため当日見出しから ${RECENT_FALLBACK_LINES} 段落で打ち切りました。沈殿が混入します`);
  }

  // ゾーン内に残る過去日の見出しは、ブロックの区切りとして扱い（見出し行が項目に混ざらない）、
  // 各ブロックがどの日に書かれたかを date として残す。
  const slice = paras.slice(start.index + 1, endExclusive).map((p) => {
    const ymd = p.h1 ? ymdOfHeading(p.text) : null;
    return ymd ? { ...p, text: '', dateYmd: ymd } : p;
  });
  const boundaries = slice.filter((p) => p.dateYmd).map((p) => ({ start: p.start, ymd: p.dateYmd }));
  const blocks = toBlocks(slice, start.index + 1);
  for (const b of blocks) {
    let ymd = start.ymd;
    for (const bd of boundaries) if (b.docIndex > bd.start) ymd = bd.ymd;
    b.date = ymd;
  }
  // ゾーン内の「# 見出し」は章見出しではなく直後数行の話題札にすぎない。
  // 無制限に伝播させると無関係なブロックに誤った文脈が付く（`# BBQ、皆で話したことまとめ` が
  // 40ブロック先の ISO の項目にまで及んだ実例あり）。距離を添えて CONTEXT_REACH で打ち切る。
  let context = null;
  let distance = 0;
  for (const b of blocks) {
    const first = b.lines[0].text;
    if (first.startsWith('#')) {
      context = first.replace(/^#+\s*/, '');
      distance = 0;
      b.isHeading = true;
      continue;
    }
    distance++;
    if (context && distance <= CONTEXT_REACH) {
      b.context = context;
      b.contextDistance = distance;
    }
  }
  return { found: true, sinkFound, dateHeading: start.text, startYmd: start.ymd, isToday: start.ymd === today, blocks };
}

// ── Apple Notes ────────────────────────────────────────
function fetchNotes() {
  if (NO_NOTES) return { found: false, skipped: true, lines: [] };
  const script = `
tell application "Notes"
  set matches to every note whose name is "${NOTES_TITLE}"
  if (count of matches) is 0 then return "NOTFOUND"
  return body of item 1 of matches
end tell`;
  let body;
  try {
    body = execFileSync('osascript', ['-e', script], { encoding: 'utf8', timeout: 20_000 });
  } catch (e) {
    warn(`Apple Notes を読めませんでした（自動化許可か Notes.app の状態を確認）: ${String(e.message).slice(0, 120)}`);
    return { found: false, error: true, lines: [] };
  }
  if (body.trim() === 'NOTFOUND') {
    warn(`Apple Notes に「${NOTES_TITLE}」ノートがありません。action-items.md「出口の設置」参照`);
    return { found: false, lines: [] };
  }
  // HTML → 行。1行目はノートのタイトルなので落とす
  const lines = body
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(div|p|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .split('\n').map((s) => s.trim()).filter(Boolean);
  if (lines[0] === NOTES_TITLE) lines.shift();
  return { found: true, lines };
}

// ── top-priority.md ─────────────────────────────────────
const TOP_PRIORITY_PATH = INST.file('top-priority.md');

/**
 * top-priority.md を `## ` 節ごとに構造化する。タスクの正本は各プロジェクトの
 * action-items.md のまま（docs/design.md「使い手が指定した最優先課題」）なので、内容の転記や
 * 「今日提示してよいか」の判定はしない。判定材料として hasCondition を立てるに留め、
 * 条件を満たすかどうかの判断は合成層（today.md 生成時のセッション）に委ねる。
 */
function collectTopPriority() {
  let raw;
  try {
    raw = readFileSync(TOP_PRIORITY_PATH, 'utf8');
  } catch (e) {
    warn(`top-priority.md を読めませんでした: ${String(e.message).slice(0, 100)}`);
    return { found: false, sections: [] };
  }
  const lines = raw.split('\n');
  const sections = [];
  let cur = null;
  for (const line of lines) {
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) {
      cur = { title: h2[1].trim(), lines: [] };
      sections.push(cur);
      continue;
    }
    if (cur) cur.lines.push(line);
  }
  const parsed = sections.map((s) => {
    const body = s.lines.join('\n').trim();
    return {
      title: s.title,
      body,
      hasCondition: /条件/.test(body),
      commands: [...body.matchAll(/```bash\n([\s\S]*?)```/g)].map((m) => m[1].trim()),
    };
  });
  return { found: true, path: relative(INST.dir, TOP_PRIORITY_PATH), sections: parsed };
}

// ── Gmail inbox ──────────────────────────────────────────
const GMAIL_CRED_PATH = CFG.gmail?.credential;
const GMAIL_ACCOUNT = CFG.gmail?.account;
const GMAIL_MAX_RESULTS = 50;

// Gmail のユーザー単位同時実行数上限に当たらないよう、並行数を絞って取得する
async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  });
  await Promise.all(workers);
  return results;
}

// q で引いた通の件名・差出人・宛先・snippet・リンクを返す。inbox と送信済みで共用する
async function fetchGmailList(q, maxResults, label) {
  if (NO_GMAIL) return { found: false, skipped: true, resultSizeEstimate: null, messages: [] };
  let token;
  try {
    token = await accessToken(GMAIL_CRED_PATH);
  } catch (e) {
    warn(`Gmail の認証に失敗: ${String(e.message).slice(0, 120)}`);
    return { found: false, resultSizeEstimate: null, messages: [] };
  }
  const listUrl = new URL('https://gmail.googleapis.com/gmail/v1/users/me/messages');
  listUrl.searchParams.set('q', q);
  listUrl.searchParams.set('maxResults', String(maxResults));
  const listRes = await fetch(listUrl, { headers: { Authorization: `Bearer ${token}` } });
  const listData = await listRes.json();
  if (!listRes.ok) {
    warn(`Gmail ${label} 取得に失敗: ${JSON.stringify(listData).slice(0, 200)}`);
    return { found: false, resultSizeEstimate: null, messages: [] };
  }
  const ids = (listData.messages || []).map((m) => m.id);
  const messages = await mapLimit(ids, 5, async (id) => {
    const url = new URL(`https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}`);
    url.searchParams.set('format', 'metadata');
    for (const h of ['Subject', 'From', 'To', 'Cc', 'Date']) url.searchParams.append('metadataHeaders', h);
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    const data = await res.json();
    if (!res.ok) return null;
    const headers = Object.fromEntries((data.payload?.headers || []).map((h) => [h.name, h.value]));
    return {
      id,
      threadId: data.threadId,
      subject: headers.Subject || '(件名なし)',
      from: headers.From || null,
      to: headers.To || null,
      cc: headers.Cc || null,
      date: headers.Date || null,
      snippet: data.snippet || '',
      link: `https://mail.google.com/mail/?authuser=${GMAIL_ACCOUNT}#all/${id}`,
    };
  });
  return {
    found: true,
    // Gmail 検索は総件数を返さない。resultSizeEstimate は maxResults 内の概算で、
    // 滞留の実数を知るには docs/design.md「Gmail inbox の節」の区間クエリを別途使う
    resultSizeEstimate: listData.resultSizeEstimate ?? null,
    messages: messages.filter(Boolean),
  };
}

const fetchGmailInbox = () => fetchGmailList('in:inbox', GMAIL_MAX_RESULTS, 'inbox');

// 1件ずつの完了確認（docs/design.md）の材料。予約送信は in:scheduled にしか無いので併せて引く。
// 👍 などのリアクションも送信済みに1通として入る
const SENT_DAYS = 7;
const SENT_MAX = 150;
function fetchGmailSent(today) {
  const since = Math.floor(Date.parse(`${addDays(today, -SENT_DAYS)}T00:00:00+09:00`) / 1000);
  // 自前の仕組みが自分宛に送る通知（異常・まとめ・アラート）は人の送信ではないので除く。件名は設定の gmail.sentExcludeSubjects
  const auto = (CFG.gmail?.sentExcludeSubjects || []).map((t) => `-subject:"${t}"`).join(' ');
  return fetchGmailList(`(in:sent OR in:scheduled) after:${since}${auto ? ` ${auto}` : ''}`, SENT_MAX, '送信済み');
}

// ── 台帳 ───────────────────────────────────────────────
/**
 * 日付らしいトークンを拾い、位置と年月日を持たせる。素朴に \d+/\d+ を拾うと金額（2500/3500ドル）・
 * 連番（23/24 連続登壇）・部分日付（2026/4/13 の 26/4）まで日付扱いになるため、月1-12・日1-31 に制限し、
 * 前後に数字と / が続かないことを条件にする。
 * 上から順に拾い、先に拾った範囲と重なるものは捨てる（2026/7/31 の中の 7/31、2026-07-31 の中の 2026-07）。
 * 日の無いものは、上旬を10日・中旬を20日とし、それ以外（下旬・末・中・月だけ）は月末として扱う。
 */
const dayOfPart = (s) => (s === '上旬' ? 10 : s === '中旬' ? 20 : null);
const DATE_TOKEN_RES = [
  [/(\d{4})-(\d{2})-(\d{2})/g, (m) => [+m[1], +m[2], +m[3]]],                          // 2026-07-31
  [/(\d{4})\/(\d{1,2})\/(\d{1,2})/g, (m) => [+m[1], +m[2], +m[3]]],                    // 2026/7/31
  [/(?<!\d)(\d{4})-(1[0-2]|0?[1-9])(?!-?\d)(?:-?(上旬|中旬|下旬|中|末))?/g,               // 2026-11 / 2026-09中旬
    (m) => [+m[1], +m[2], dayOfPart(m[3])]],
  [/(?:(\d{4})年)?(?<!\d)(\d{1,2})月(?:(\d{1,2})日|(上旬|中旬|下旬|末|中))?/g,           // 2027年4月 / 10月下旬 / 8月31日
    (m) => [m[1] ? +m[1] : null, +m[2], m[3] ? +m[3] : dayOfPart(m[4])]],
  [/(?<![\d/])(1[0-2]|0?[1-9])\/(3[01]|[12]\d|0?[1-9])(?![\d/])/g, (m) => [null, +m[1], +m[2]]], // 7/31
];
// 期限語。日付トークンと同じ項目に共起したときだけ「期限」と見なす
// 「目安」（旧来の「目標日」「目標：」も同じ）は自分で決めた日付で、期限ではない（docs/design.md「期限の扱い」の「期日と目安の区別」）。
// 日付と共起したら kind: 'self' として読むが、期限超過にも期限の点検にも入れない
const DEADLINE_RE = /〆切?|締切|期限|必着|ASAP|までに|目安|目標日|目標[：:]/;
const SELF_GOAL_RE = /^(?:目安|目標)/;
// 期日の語だけ。止まっている台帳を読み続ける条件に使う（目安だけの台帳は起こさない）
const EXTERNAL_DEADLINE_RE = /〆切?|締切|期限|必着|ASAP|までに/;
// 期限語を含むが期限を示さない言い回し。期限語を探す前に伏せる（「期限の扱い」は docs/design.md の節の名前）
const NOT_DEADLINE_RE = /期限(?:超過|切れ|の扱い|の記載(?:な|無)し|は?(?:な|無)し|[はを]付けていない|を引き直)/g;
// 期限語からこの文字数以内にある日付を、その期限語の期限とする
const DEADLINE_REACH = 12;

function dateTokensOf(text) {
  const out = [];
  for (const [re, parts] of DATE_TOKEN_RES) {
    for (const m of text.matchAll(re)) {
      const s = m.index, e = s + m[0].length;
      if (out.some((t) => s < t.e && e > t.s)) continue;
      out.push({ token: m[0], s, e, parts: parts(m) });
    }
  }
  return out.sort((a, b) => a.s - b.s);
}

const daysBetween = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000);

// 年の無い日付には、今日から前後半年の範囲で最も近い年を補う。
// 半年以上前に過ぎた年なしの期限は先の日付として読まれるので、台帳には年付きで書く（docs/design.md「期限の扱い」）
function ymdOf([y, mo, d], today) {
  const at = (yy) => `${yy}-${String(mo).padStart(2, '0')}-${String(d ?? new Date(Date.UTC(yy, mo, 0)).getUTCDate()).padStart(2, '0')}`;
  if (y) return at(y);
  const ty = Number(today.slice(0, 4));
  return [ty - 1, ty, ty + 1].map(at).sort((a, b) => Math.abs(daysBetween(today, a)) - Math.abs(daysBetween(today, b)))[0];
}

/**
 * 期限語に隣接する日付を期限として返す。「X までに」は日付が前に来るので前だけを見る。
 * 1行に期限が複数あるときは最も遅いものを採る（内部の締切を経た最終の期限が、その行の期限）。
 * overdueDays は今日から期限を引いた日数で、正が超過、0 が今日、負が残り日数。
 * kind は external（外部から設定された期限）か self（「目安」「目標日」「目標：」で書いた、自分で決めた日付）。
 */
function deadlineOf(masked, tokens, today) {
  const picks = [];
  for (const w of masked.matchAll(new RegExp(DEADLINE_RE, 'g'))) {
    const ws = w.index, we = ws + w[0].length;
    let best = null;
    for (const t of tokens) {
      if (w[0] === 'までに' && t.s >= we) continue;
      const dist = t.e <= ws ? ws - t.e : t.s >= we ? t.s - we : 0;
      if (dist <= DEADLINE_REACH && (!best || dist < best.dist)) best = { ...t, dist };
    }
    if (best) picks.push({ token: best.token, at: best.s, ymd: ymdOf(best.parts, today), kind: SELF_GOAL_RE.test(w[0]) ? 'self' : 'external' });
  }
  // 目安が指した日付は、近くの期限語が同じ日付を指しても期日にしない（「期限を見直す（目安 11/13）」）
  const selfAt = new Set(picks.filter((p) => p.kind === 'self').map((p) => p.at));
  for (let i = picks.length - 1; i >= 0; i--) if (picks[i].kind === 'external' && selfAt.has(picks[i].at)) picks.splice(i, 1);
  if (!picks.length) return null;
  // 外部期限があればそれを採る（目安より外部期限の方が行の期限として重い）。無ければ目安
  const ext = picks.filter((p) => p.kind === 'external');
  const last = (ext.length ? ext : picks).sort((a, b) => b.ymd.localeCompare(a.ymd))[0];
  const { at, ...pick } = last;
  return { ...pick, overdueDays: daysBetween(last.ymd, today) };
}

// 設定の ledgers.roots（~/Projects からの相対。"." は全体）の下の台帳から、ledgers.exclude の下にあるものを除く
const underExcluded = (p) => CFG.ledgers.exclude.some((x) => { const r = relative(join(PROJECTS, x), p); return r && !r.startsWith('..'); });
const LEDGER_PATHS = [...new Set(CFG.ledgers.roots.flatMap((r) => walkLedgers(join(PROJECTS, r))))].filter((p) => !underExcluded(p));

function collectLedgers(today) {
  const paths = [...LEDGER_PATHS];
  const cutoff = Date.now() - LEDGER_DAYS * 86_400_000;
  const ledgers = [];
  let skipped = 0;
  // 読まなかった（止まっている）台帳も、未処理の件数と止まっている日数だけは出す。棚卸しの候補になる
  const staleList = [];
  for (const p of paths.sort()) {
    // mtime と名乗るが、中身は台帳を最後に変えた時刻（ledgers.mjs の lastChanged。clone・pull で新しくならない）
    const st = { mtimeMs: lastChanged(p).getTime() };
    const lines = readLines(p);
    // 鮮度を過ぎた台帳も、期日・待ち・[!] の未処理行があれば読む（目安だけの行では読まない）。止まっている台帳ほど見落とすと危ない
    const stale = st.mtimeMs < cutoff;
    if (stale && !lines.some((l) => /^\s*- \[!\]\s+/.test(l) || (/^\s*- \[ \]\s+/.test(l) && (/待ち：/.test(l) || (EXTERNAL_DEADLINE_RE.test(l.replace(NOT_DEADLINE_RE, '')) && dateTokensOf(l).length))))) {
      skipped++;
      const open = lines.filter((l) => /^\s*- \[ \]\s+/.test(l)).length;
      if (open) staleList.push({ path: relPath(PROJECTS, p), mtime: new Date(st.mtimeMs).toISOString().slice(0, 10), staleDays: daysBetween(new Date(st.mtimeMs).toISOString().slice(0, 10), today), openCount: open });
      continue;
    }
    let section = null;
    const items = [];
    lines.forEach((raw, i) => {
      const h = raw.match(/^(#{2,3})\s+(.+)$/);
      if (h) { section = h[2].trim(); return; }
      const m = raw.match(/^(\s*)- \[ \]\s+(.+)$/);
      if (!m) return;
      const text = m[2].trim();
      const tokens = dateTokensOf(text);
      // 位置を保ったまま伏せる（期限語と日付の距離を測るため）
      const masked = text.replace(NOT_DEADLINE_RE, (s) => '＿'.repeat(s.length));
      const hasDeadlineWord = DEADLINE_RE.test(masked);
      const item = {
        line: i + 1,
        section,
        nested: m[1].length > 0,
        bold: /\*\*/.test(text),
        dateTokens: tokens.map((t) => t.token),
        // 日付トークンだけでは過去の出来事への言及（「6/22 小寺面談で確認」）と区別できない。
        // 期限語が共起したものだけ期限候補として立てる
        deadlineCandidate: hasDeadlineWord && tokens.length > 0,
        text,
      };
      const deadline = item.deadlineCandidate ? deadlineOf(masked, tokens, today) : null;
      if (deadline) item.deadline = deadline;
      items.push(item);
    });
    if (items.length) {
      ledgers.push({
        path: relPath(PROJECTS, p),
        mtime: new Date(st.mtimeMs).toISOString().slice(0, 10),
        ...(stale ? { stale: true, staleDays: daysBetween(new Date(st.mtimeMs).toISOString().slice(0, 10), today) } : {}),
        openCount: items.length,
        items,
      });
    }
  }
  // 期日（kind が external）を持つ未完了の行だけを数える。目安（self）は過ぎても警報を出さない（docs/design.md「期限の扱い」）
  const rows = (pred) => ledgers.flatMap((l) => l.items.filter((it) => it.deadline && pred(it.deadline))
    .map(({ line, section, deadline, text }) => ({ path: l.path, line, section, deadline, text })));
  // 期限を過ぎた未完了の行。today.md の「やること」の先頭に【期限超過】として出す。
  // 過ぎたばかりのものほど取り返しが利くので、超過日数の短い順に並べる
  const overdue = rows((d) => d.kind === 'external' && d.overdueDays > 0)
    .sort((a, b) => a.deadline.overdueDays - b.deadline.overdueDays);
  // 期限がこれから来る未完了の行。遠い期限も落とさず「直近の締切」「先の締切」に出す。期日の近い順
  const dueSoon = rows((d) => d.kind === 'external' && d.overdueDays <= 0)
    .sort((a, b) => a.deadline.ymd.localeCompare(b.deadline.ymd));
  // 目安を持つ未完了の行。過ぎたものも含め、優先順位を付ける材料の1つとして渡すだけで、today.md に載せることは求めない
  const aims = rows((d) => d.kind === 'self').sort((a, b) => a.deadline.ymd.localeCompare(b.deadline.ymd));
  staleList.sort((a, b) => b.openCount - a.openCount);
  return { ledgers, overdue, dueSoon, aims, staleSkipped: skipped, staleList, scanned: paths.length };
}

// ── 意識しておく案件 ─────────────────────────────────────
// インスタンスの keep-in-mind.md（docs/design.md「意識しておく案件」）。日付が無くても大事な案件を today.md から落とさないため、
// 案件ごとに台帳（節まで指せる）の次の一手（最初の未処理行）、最も近い期日、止まっている日数を出す。並べるのは合成層
const KEEP_PATH = INST.file('keep-in-mind.md');
const KEEP_IDLE_DAYS = 14;

function collectKeepInMind(today) {
  if (!existsSync(KEEP_PATH)) return { found: false, items: [] };
  const items = [];
  let cur = null;
  for (const line of readFileSync(KEEP_PATH, 'utf8').split('\n')) {
    const h = line.match(/^##\s+(.+)$/);
    if (h) { cur = { title: h[1].trim(), order: items.length + 1, refs: [], note: [] }; items.push(cur); continue; }
    if (!cur) continue;
    const l = line.match(/^台帳[：:]\s*(.+)$/);
    if (l) cur.refs.push(...l[1].split(/[、,]\s*/).map((r) => r.replace(/`/g, '').trim()).filter(Boolean));
    else if (line.trim()) cur.note.push(line.trim());
  }
  return {
    found: true,
    idleDays: KEEP_IDLE_DAYS,
    items: items.map(({ title, order, refs, note }) => ({
      title, order, note: note.join(' '),
      ledgers: refs.map((ref) => {
        const [path, section = null] = ref.split('#');
        const abs = join(PROJECTS, path);
        if (!existsSync(abs)) return { path, section, missing: true };
        const lines = readLines(abs);
        const open = [];
        let sec = null;
        lines.forEach((raw, i) => {
          const hh = raw.match(/^#{2,3}\s+(.+)$/);
          if (hh) { sec = hh[1].trim(); return; }
          const m = raw.match(/^\s*- \[( |!)\]\s+(.+)$/);
          if (!m || (section && sec !== section)) return;
          const text = m[2].trim();
          const masked = text.replace(NOT_DEADLINE_RE, (x) => '＿'.repeat(x.length));
          const tokens = dateTokensOf(text);
          const deadline = DEADLINE_RE.test(masked) && tokens.length ? deadlineOf(masked, tokens, today) : null;
          open.push({ line: i + 1, blocked: m[1] === '!', text, ...(deadline ? { deadline } : {}) });
        });
        const ext = open.filter((o) => o.deadline?.kind === 'external').sort((a, b) => a.deadline.ymd.localeCompare(b.deadline.ymd));
        const mtime = lastChanged(abs).toISOString().slice(0, 10);
        const idle = daysBetween(mtime, today);
        return {
          path, section, openCount: open.length,
          next: open[0] ? { line: open[0].line, blocked: open[0].blocked, text: open[0].text } : null,
          nearestDeadline: ext[0] ? { ymd: ext[0].deadline.ymd, overdueDays: ext[0].deadline.overdueDays, line: ext[0].line } : null,
          idleDays: idle, idle: idle >= KEEP_IDLE_DAYS,
        };
      }),
    })),
  };
}

// ── 返事待ちの検知 ─────────────────────────────────────
// 台帳の「待ち：<アドレス> <照会日>」を拾い、その相手から照会日以降にメールが来ていないかを Gmail で調べる。
// 返事が届いたのに台帳が「待ち」のまま止まり、次の一手が自分に戻ったことが見えなくなるのを防ぐ。
// 書式の正本は claude-toolkit の conventions/action-items-convention.md「返事待ちの行」。
// 鮮度で除外した台帳も対象にする（待っている間は台帳が更新されないため）。

async function checkReplies(waiting) {
  if (NO_GMAIL || !waiting.length) return { checked: false, items: waiting };
  let token;
  try {
    token = await accessToken(GMAIL_CRED_PATH);
  } catch (e) {
    warn(`返事待ちの検知で Gmail の認証に失敗: ${String(e.message).slice(0, 120)}`);
    return { checked: false, items: waiting };
  }
  const items = await mapLimit(waiting, 5, async (w) => {
    // 照会日の 00:00（日本時間）以降を UNIX 秒で指定する。日付形式の after: はその日を含むうえ、境界がアカウントの
    // タイムゾーンで決まるため、前日を渡すと前日に届いた返信まで拾っていた（2026-10-11、照会前日の受領連絡を返事と数えた）。
    // from: なので自分の照会そのものは拾わない
    const since = Math.floor(Date.parse(`${w.since}T00:00:00+09:00`) / 1000);
    const q = `(${w.addrs.map((a) => `from:${a}`).join(' OR ')}) after:${since}`;
    const url = new URL('https://gmail.googleapis.com/gmail/v1/users/me/messages');
    url.searchParams.set('q', q);
    url.searchParams.set('maxResults', '5');
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    const data = await res.json();
    if (!res.ok) { warn(`返事待ちの検索に失敗（${w.path}:${w.line}）`); return { ...w, query: q, replies: null }; }
    const replies = await mapLimit((data.messages || []).map((m) => m.id), 5, async (id) => {
      const u = new URL(`https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}`);
      u.searchParams.set('format', 'metadata');
      for (const h of ['Subject', 'From', 'Date']) u.searchParams.append('metadataHeaders', h);
      const r = await fetch(u, { headers: { Authorization: `Bearer ${token}` } });
      const d = await r.json();
      if (!r.ok) return null;
      const hd = Object.fromEntries((d.payload?.headers || []).map((h) => [h.name, h.value]));
      return { id, subject: hd.Subject || '(件名なし)', from: hd.From || null, date: hd.Date || null,
        link: `https://mail.google.com/mail/?authuser=${GMAIL_ACCOUNT}#all/${id}` };
    });
    return { ...w, query: q, replies: replies.filter(Boolean) };
  });
  return { checked: true, items };
}

// ── 返していない受信を引く台帳の行 ──────────────────────
// 台帳の未処理行（`- [ ]`・`- [!]`）が Gmail の ID を引き、その通が受信トレイに残っていて、使い手宛（To に使い手のアドレス。Cc だけのものは
// 除く。差出人が noreply の通知も除く）に届いていて、その後同じスレッドに使い手の送信・予約送信が無いものを出す。check-today.mjs が today.md で
// 参照されているかを点検する。2026-11-08、期限の無い台帳の行が引く受信（返事が要る）が、inbox の節からは「台帳に
// 行がある」として外され、「やること」には期限が無いので上がらず、2週間 today.md から抜けていた。
// 鮮度で除外した台帳も対象にする（止まっている台帳ほど見落とすため）。
const GMAIL_ID_RE = /(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])/g;
async function fetchBallReturned(paths) {
  if (NO_GMAIL) return { checked: false, items: [] };
  const rowsById = new Map();
  for (const p of paths) {
    readLines(p).forEach((raw, i) => {
      if (!/^\s*- \[[ !]\]\s+/.test(raw)) return;
      for (const id of new Set([...raw.matchAll(GMAIL_ID_RE)].map((m) => m[1]))) {
        if (!rowsById.has(id)) rowsById.set(id, []);
        rowsById.get(id).push({ path: relPath(PROJECTS, p), line: i + 1, text: raw.replace(/^\s*- \[[ !]\]\s+/, '').trim() });
      }
    });
  }
  if (!rowsById.size) return { checked: true, ids: 0, items: [] };
  let token;
  try {
    token = await accessToken(GMAIL_CRED_PATH);
  } catch (e) {
    warn(`返していない受信の検知で Gmail の認証に失敗: ${String(e.message).slice(0, 120)}`);
    return { checked: false, items: [] };
  }
  const get = async (path) => {
    const r = await fetch(`https://gmail.googleapis.com/gmail/v1/users/me/${path}`, { headers: { Authorization: `Bearer ${token}` } });
    return r.ok ? r.json() : null;
  };
  const myAddrs = await myAddresses(get);
  const META = 'format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Cc&metadataHeaders=Subject';
  const found = await mapLimit([...rowsById.keys()], 5, async (id) => {
    const msg = await get(`messages/${id}?${META}`);
    if (!msg || kindOf(msg, myAddrs) !== '届いた') return null;
    // アーカイブした通は済んだものとして扱う（使い手が外したなら返事が要らないか対応済み、対話のセッションが外したなら
    // 反応は済んでいる。2026-11-08 決定）。受信トレイに残っている通だけを見る
    if (!(msg.labelIds || []).includes('INBOX')) return null;
    const to = header(msg, 'To').toLowerCase();
    if (!myAddrs.some((a) => to.includes(a))) return null;
    // 返信を受けない通知（Google のコメント通知など）は、返すものが無い
    if (/no-?reply/i.test(header(msg, 'From'))) return null;
    const th = await get(`threads/${msg.threadId}?format=metadata&metadataHeaders=From`);
    const answered = (th?.messages || []).some((m) => Number(m.internalDate) > Number(msg.internalDate) && ['送った', '予約した'].includes(kindOf(m, myAddrs)));
    if (answered) return null;
    const mail = { id, subject: header(msg, 'Subject') || '(件名なし)', from: header(msg, 'From'),
      date: new Date(Number(msg.internalDate)).toISOString(), link: `https://mail.google.com/mail/?authuser=${GMAIL_ACCOUNT}#all/${id}` };
    return rowsById.get(id).map((r) => ({ ...r, mail }));
  });
  return { checked: true, ids: rowsById.size, items: found.filter(Boolean).flat() };
}

// ── 督促の検知 ─────────────────────────────────────────
// 返事待ちの行が REMIND_DAYS 日を過ぎても返事が無いとき、督促が要る項目として出す。
// 行には「待ち：<アドレス> <最終の連絡日> <N>回目」と書く。N は連絡の通算回数（初回の照会が1回目、省略時は1）。
// 次の連絡は N+1 回目になり、宛先の追加（上長など）は escalation.csv が回数で決める。
// 書式の正本は claude-toolkit の conventions/action-items-convention.md「返事待ちの行」、
// 運用は docs/design.md「督促の扱い」。送った督促は行の日付と回数を書き換えて数える（自動では数えない）。
const REMIND_DAYS = 7;

function loadEscalation() {
  const p = INST.file('escalation.csv');
  if (!existsSync(p)) return [];
  const [head, ...rows] = readFileSync(p, 'utf8').replace(/^\uFEFF/, '').trim().split('\n');
  const cols = head.split(',');
  return rows.filter((r) => r.trim()).map((r) => {
    const v = r.split(',');
    const o = Object.fromEntries(cols.map((c, i) => [c.trim(), (v[i] || '').trim()]));
    return { addr: o['アドレス'].toLowerCase(), group: o['先'], from: Number(o['N回目から']), cc: o['Cc追加'].split(';').filter(Boolean), note: o['備考'] };
  });
}

async function buildReminders(waiting, sent, ymd) {
  const esc = loadEscalation();
  const due = waiting.items.filter((w) => w.replies && !w.replies.length && daysBetween(w.since, ymd) >= REMIND_DAYS);
  let token = null;
  if (due.length && !NO_GMAIL) { try { token = await accessToken(GMAIL_CRED_PATH); } catch { token = null; } }
  return mapLimit(due, 5, async (w) => {
    const next = w.count + 1;
    const rows = esc.filter((e) => w.addrs.some((a) => a.toLowerCase() === e.addr) && e.from <= next);
    const ccAdd = [...new Set(rows.flatMap((e) => e.cc))];
    const needSuperior = next >= 3 && !rows.some((e) => e.from >= 3);
    // 最終の連絡日より後に、同じ相手へ送った通があれば、督促を送った可能性がある（台帳の行の直し忘れ）
    const since = Date.parse(`${w.since}T00:00:00+09:00`);
    const sentAfter = (sent.messages || []).filter((m) => Date.parse(m.date) > since &&
      w.addrs.some((a) => `${m.to || ''} ${m.cc || ''}`.toLowerCase().includes(a.toLowerCase()))).map((m) => ({ id: m.id, subject: m.subject, date: m.date }));
    let drafts = [];
    if (token) {
      const q = `in:drafts (${w.addrs.map((a) => `to:${a} OR cc:${a}`).join(' OR ')})`;
      const url = new URL('https://gmail.googleapis.com/gmail/v1/users/me/messages');
      url.searchParams.set('q', q);
      url.searchParams.set('maxResults', '3');
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      const data = await res.json();
      if (res.ok) drafts = (data.messages || []).map((m) => m.id);
    }
    return { path: w.path, line: w.line, addrs: w.addrs, since: w.since, daysSince: daysBetween(w.since, ymd), count: w.count, nextCount: next,
      ccAdd, needSuperior, sentAfter, draftIds: drafts, text: w.text };
  });
}

// ── カレンダー ─────────────────────────────────────────
/** ファイルの予定表を読み、注意を warn に流す。読めなければ null */
function fileItems(cal, fromYmd, toYmd) {
  const w = [];
  const items = fileEvents(cal, fromYmd, toYmd, w);
  w.forEach((m) => warn(m));
  return items;
}

/** Google の予定表の ymd から days 日分の生の予定。読めなければ null */
async function googleDayItems({ credential: credName, label, calendarId = 'primary' }, ymd, days) {
  let token;
  try {
    token = await accessToken(credName);
  } catch (e) {
    warn(`${label} カレンダーの認証に失敗: ${String(e.message).slice(0, 120)}`);
    return null;
  }
  const q = new URLSearchParams({
    timeMin: `${ymd}T00:00:00+09:00`,
    timeMax: `${addDays(ymd, days)}T00:00:00+09:00`,
    singleEvents: 'true',
    orderBy: 'startTime',
    maxResults: '250',
    timeZone: TZ,
  });
  const res = await fetch(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events?${q}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();
  if (!res.ok) { warn(`${label} カレンダー取得に失敗: ${JSON.stringify(data).slice(0, 200)}`); return null; }
  return data.items || [];
}

/**
 * ymd から days 日分の予定を取る。複数日のときは各予定に date を付ける。
 * 辞退した予定は除く（カレンダーに残っていても出席しない）
 */
async function fetchDayEvents(cal, ymd, days = 1) {
  const items = isFileCalendar(cal) ? fileItems(cal, ymd, addDays(ymd, days)) : await googleDayItems(cal, ymd, days);
  if (!items) return [];
  const { label } = cal;
  const out = [];
  for (const ev of items) {
    if (ev.status === 'cancelled') continue;
    const me = (ev.attendees || []).find((a) => a.self);
    if (me && me.responseStatus === 'declined') continue;
    const title = (ev.summary || '(無題)').trim();
    const kind = classifyEventTitle(title);
    const others = (ev.attendees || []).filter((a) => !a.self).map((a) => a.displayName || a.email);
    const date = days > 1 ? { date: ev.start.date || ev.start.dateTime.slice(0, 10) } : {};
    if (ev.start.date) {
      out.push({ account: label, ...date, kind, allDay: true, title, location: ev.location || null, attendees: others });
    } else {
      out.push({
        account: label,
        ...date,
        kind,
        allDay: false,
        start: hhmm(ev.start.dateTime),
        end: hhmm(ev.end.dateTime),
        title,
        location: ev.location || null,
        attendees: others,
      });
    }
  }
  return out;
}

/**
 * 複数の予定表の予定を1つの並びにまとめる。同じ日・同じ時刻・同じ題名の予定は1件にし、先に並べた予定表の方を残す。
 * 予定表が1つのときはそのまま返す（API の並びを保つ）
 */
function mergeEvents(lists) {
  if (lists.length <= 1) return lists[0] || [];
  const seen = new Set();
  const out = [];
  for (const e of lists.flat()) {
    const k = [e.date, e.allDay, e.start, e.end, e.title].join('|');
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(e);
  }
  const at = (e) => `${e.date || ''} ${e.allDay ? '' : e.start}`;
  return out.sort((a, b) => at(a).localeCompare(at(b)));
}
const fromCalendars = async (...args) => mergeEvents(await Promise.all(CALENDARS.map((c) => fetchDayEvents(c, ...args))));

/**
 * 1つの予定表の先 days 日の予定を、重なりの判定に渡す生の形で取る。辞退した予定は除く。
 * 取れなければ null
 */
async function fetchLiveEvents(cal, ymd, days) {
  const { credential: credName, label, calendarId = 'primary' } = cal;
  if (isFileCalendar(cal)) return fileItems(cal, ymd, addDays(ymd, days));
  let token;
  try {
    token = await accessToken(credName);
  } catch (e) {
    warn(`${label} カレンダーの認証に失敗: ${String(e.message).slice(0, 120)}`);
    return null;
  }
  const items = [];
  let pageToken;
  do {
    const q = new URLSearchParams({
      timeMin: `${ymd}T00:00:00+09:00`,
      timeMax: `${addDays(ymd, days)}T00:00:00+09:00`,
      singleEvents: 'true',
      orderBy: 'startTime',
      maxResults: '2500',
      timeZone: TZ,
      ...(pageToken ? { pageToken } : {}),
    });
    const res = await fetch(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events?${q}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();
    if (!res.ok) { warn(`${label} カレンダー取得に失敗: ${JSON.stringify(data).slice(0, 200)}`); return null; }
    items.push(...(data.items || []));
    pageToken = data.nextPageToken;
  } while (pageToken);
  return items.filter((ev) => ev.status !== 'cancelled'
    && (ev.attendees || []).find((a) => a.self)?.responseStatus !== 'declined');
}

/**
 * 先1か月の予定の重なりを拾う（判定の正本は conflicts.mjs、設定はインスタンスの conflict-rules.json）。
 * 予定表が複数あれば、まとめた後の並びで見る（同じ予定の重複は findConflicts が1件にする）。
 * 1つでも読めない予定表があれば checked にしない（重なりを見落としている）
 */
async function fetchConflicts(ymd) {
  if (!CONFLICT_RULES) {
    warn('conflict-rules.json が無いため予定の重なりを見ていません');
    return { checked: false, items: [] };
  }
  const days = CONFLICT_RULES.lookaheadDays;
  const lists = await Promise.all(CALENDARS.map((c) => fetchLiveEvents(c, ymd, days)));
  if (!lists.length || lists.some((l) => l === null)) return { checked: false, items: [] };
  const { eventFacts, findConflicts } = conflictChecker(CONFLICT_RULES);
  return { checked: true, from: ymd, days, ...findConflicts(lists.flat().map(eventFacts)) };
}

// ── 勤務先への連絡 ─────────────────────────────────────
/** Gmail の本文（text/plain）を取り出す。無ければ snippet */
function plainText(payload, snippet) {
  const walk = (p) => {
    if (!p) return '';
    if (p.mimeType === 'text/plain' && p.body?.data) return Buffer.from(p.body.data, 'base64url').toString('utf8');
    return (p.parts || []).map(walk).find(Boolean) || '';
  };
  return walk(payload) || snippet || '';
}

// 先の日付を書いた連絡を、何日前まで遡って探すか
const NOTICE_SENT_DAYS = 45;

/** notifyTo へ送った通（予約送信を含む）を本文ごと取る。読めなければ null */
async function fetchNoticeMails(addrs, ymd) {
  if (NO_GMAIL) return null;
  let token;
  try { token = await accessToken(GMAIL_CRED_PATH); } catch (e) {
    warn(`勤務先への連絡の照合で Gmail の認証に失敗: ${String(e.message).slice(0, 120)}`);
    return null;
  }
  const since = Math.floor(Date.parse(`${addDays(ymd, -NOTICE_SENT_DAYS)}T00:00:00+09:00`) / 1000);
  const who = addrs.map((a) => `to:${a} OR cc:${a}`).join(' OR ');
  const list = async (q) => {
    const url = new URL('https://gmail.googleapis.com/gmail/v1/users/me/messages');
    url.searchParams.set('q', q);
    url.searchParams.set('maxResults', '50');
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    const data = await res.json();
    if (!res.ok) { warn(`勤務先への連絡の検索に失敗: ${JSON.stringify(data).slice(0, 200)}`); return null; }
    return (data.messages || []).map((m) => m.id);
  };
  const ids = await list(`(in:sent OR in:scheduled) (${who}) after:${since}`);
  const drafts = await list(`in:drafts (${who})`);
  if (!ids) return null;
  const mails = await mapLimit(ids, 5, async (id) => {
    const r = await fetch(`https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}?format=full`, { headers: { Authorization: `Bearer ${token}` } });
    const d = await r.json();
    if (!r.ok) return null;
    const hd = Object.fromEntries((d.payload?.headers || []).map((h) => [h.name, h.value]));
    const sent = new Date(Number(d.internalDate));
    return {
      id, subject: hd.Subject || '(件名なし)', date: hd.Date || null,
      sentYmd: new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(sent),
      text: `${hd.Subject || ''}\n${plainText(d.payload, d.snippet)}`,
      link: `https://mail.google.com/mail/?authuser=${GMAIL_ACCOUNT}#all/${id}`,
    };
  });
  return { mails: mails.filter(Boolean), draftIds: drafts || [] };
}

/**
 * 決まった曜日に勤務先へ行けない・遅れる日と、その連絡が済んでいるかを見る（判定の正本は notices.mjs、
 * 運用は docs/design.md「勤務先への連絡」）。予定の場所の判定に conflict-rules.json を使う
 */
async function fetchAttendanceNotices(ymd) {
  const rules = CFG.attendanceNotices.map(withRuleDefaults);
  if (!rules.length) return [];
  if (!CONFLICT_RULES) {
    warn('conflict-rules.json が無いため勤務先への連絡を見ていません（予定の場所の判定に使う）');
    return [];
  }
  const days = Math.max(...rules.map((r) => r.lookaheadDays));
  const [lists, holidays] = await Promise.all([
    Promise.all(CALENDARS.map((c) => fetchLiveEvents(c, ymd, days))),
    fetchHolidays(ymd, addDays(ymd, days), warn),
  ]);
  if (!lists.length || lists.some((l) => l === null)) {
    warn('予定表を読めないため勤務先への連絡を見ていません');
    return [];
  }
  if (!holidays) warn('祝日を読めないため、勤務先への連絡の対象日から祝日を除いていません');
  const { eventFacts } = conflictChecker(CONFLICT_RULES);
  const facts = lists.flat().map(eventFacts);
  const absent = new RegExp(CONFLICT_RULES.absentPattern, 'i');
  const marks = loadMarks(INST.file(MARKS_FILE));
  return Promise.all(rules.map(async (rule) => {
    const got = await fetchNoticeMails(rule.notifyTo, ymd);
    return { ...judgeRule(rule, { today: ymd, holidays, facts, absent, mails: got?.mails ?? null, marks }), draftIds: got?.draftIds ?? [] };
  }));
}

/** 当日の曜日（en-US の短縮形。routine.json のキーに合わせる） */
function weekdayJst(ymd) {
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short' }).format(new Date(`${ymd}T00:00:00Z`));
}

/** routine.json を読み、当日に適用される帯を返す */
function routineOf(ymd) {
  let cfg;
  try {
    cfg = JSON.parse(readFileSync(INST.file('routine.json'), 'utf8'));
  } catch (e) {
    warn(`routine.json を読めませんでした。生活パターンを考慮せず空き時間を出します: ${String(e.message).slice(0, 100)}`);
    return { window: { from: '06:00', to: '23:00' }, bands: [], weekday: weekdayJst(ymd) };
  }
  const wd = weekdayJst(ymd);
  const isWeekday = !['Sat', 'Sun'].includes(wd);
  // daily は通勤・出勤前の身支度で平日のみの帯。土日にも塞ぐと出勤しない日の可処分時間を過小評価する
  const bands = [...(isWeekday ? (cfg.daily || []) : []), ...((cfg.byWeekday || {})[wd] || [])]
    .map((b) => ({ ...b, s: minOf(b.from), t: minOf(b.to) }))
    .filter((b) => b.t > b.s)
    .sort((a, b) => a.s - b.s);
  for (const b of bands) if (b.unconfirmed) warn(`routine.json（${wd}）の未確認の帯：${b.label} — ${b.unconfirmed}`);
  return { window: cfg.window, workStart: cfg.workStart, eveningLowEnergy: cfg.eveningLowEnergy, weekday: wd, bands };
}

/**
 * 空き時間を求める。塞ぐのは実予定（kind==='appointment'）と routine.json の帯。
 * routine の帯は通勤・買い物・夕食準備で、カレンダーには入っていないが実際の拘束。
 *
 * カレンダーの自衛ブロック（`不在`）と勤務場所の枠は塞がず、ラベルとして空き枠に添える。
 * 予定として塞ぐと、他人の予約を防ぐために押さえただけの帯を失う。今日の 17:00-20:00 の
 * ような帯が実際に埋まっているかどうかは routine 側が決める。
 *
 * 直前直後の予定も添えて「誰と会う前後か」を判断できるようにする。
 */
function freeGaps(timed, routine) {
  const labels = timed.filter((e) => e.kind !== 'appointment')
    .map((e) => ({ s: minOf(e.start), t: minOf(e.end), title: e.title, kind: e.kind }));
  const busy = [
    ...timed.filter((e) => e.kind === 'appointment').map((e) => ({ s: minOf(e.start), t: minOf(e.end), ev: e })),
    ...routine.bands.map((b) => ({ s: b.s, t: b.t, ev: { title: b.label, kind: b.kind } })),
  ]
    .filter((b) => b.t > b.s)
    .sort((a, b) => a.s - b.s);
  const merged = [];
  for (const b of busy) {
    const last = merged[merged.length - 1];
    if (last && b.s <= last.t) { if (b.t > last.t) { last.t = b.t; last.after = b.ev; } }
    else merged.push({ s: b.s, t: b.t, before: b.ev, after: b.ev });
  }
  const gaps = [];
  let cursor = minOf(routine.window.from);
  const dayEnd = minOf(routine.window.to);
  // 直前の予定は「まとめた区間の末尾」＝直前に終わった予定。先頭を返すと連続会議の1本目が出てしまう
  let prevEvent = null;
  for (const m of merged) {
    if (m.s > cursor) {
      const to = Math.min(m.s, dayEnd);
      gaps.push({ from: timeOf(cursor), to: timeOf(to), minutes: to - cursor, prevEvent, nextEvent: m.before ? m.before.title : null });
    }
    prevEvent = m.after ? m.after.title : prevEvent;
    cursor = Math.max(cursor, m.t);
    if (cursor >= dayEnd) break;
  }
  if (cursor < dayEnd) gaps.push({ from: timeOf(cursor), to: timeOf(dayEnd), minutes: dayEnd - cursor, prevEvent, nextEvent: null });
  // 重なる自衛ブロック・勤務場所の枠をラベルとして添え、済んだ枠に印を付ける
  const nowMin = minOf(nowHhmm());
  for (const g of gaps) {
    const s = minOf(g.from);
    const t = minOf(g.to);
    const overlap = labels.filter((l) => l.s < t && l.t > s);
    if (overlap.length) {
      g.labels = overlap.map((l) => ({ title: l.title, kind: l.kind, from: timeOf(l.s), to: timeOf(l.t) }));
    }
    if (t <= nowMin) g.past = true;
    else if (s < nowMin) { g.remainingFrom = timeOf(nowMin); g.remainingMinutes = t - nowMin; }
    // 夕食後は疲れているため判断を要する作業を割り当てない
    if (routine.eveningLowEnergy && t > minOf(routine.eveningLowEnergy.from)) g.lowEnergy = true;
  }
  return gaps.filter((g) => g.minutes >= MIN_GAP_MIN);
}

// ── 実行 ───────────────────────────────────────────────
const today = todayJst();

const doc = await fetchWorkDoc();
const paras = paragraphsOf(doc);
const capture = captureZone(paras);
const recent = recentZone(paras, today);
const notes = fetchNotes();
const { ledgers, overdue, dueSoon, aims, staleSkipped, staleList, scanned } = collectLedgers(today);
const topPriority = collectTopPriority();
const keepInMind = collectKeepInMind(today);
if (!CALENDARS.length) warn('予定表の設定（calendars）が無いため、予定・空き時間・予定の重なりを出していません');
if (!CFG.gmail) warn('Gmail の設定（gmail）が無いため、inbox・送信済み・返事待ちの返事を見ていません');

const [dayEvents, upcomingRaw, conflicts, gmail, sent, waiting, attendanceNotices, ballReturned] = await Promise.all([
  fromCalendars(today),
  fromCalendars(addDays(today, 1), UPCOMING_DAYS),
  fetchConflicts(today),
  fetchGmailInbox(),
  fetchGmailSent(today),
  checkReplies(collectWaiting(PROJECTS, LEDGER_PATHS)),
  fetchAttendanceNotices(today),
  fetchBallReturned(LEDGER_PATHS),
]);
const reminders = await buildReminders(waiting, sent, today);
const mailFiles = CFG.mailFiles.map((m) => { const w = []; const r = readMailFile(m, w); w.forEach((x) => warn(x)); return r; });
const events = [...dayEvents];
// 明日以降の実予定。準備の要る予定（講義・発表）に台帳の行があるか、「日程を決める」「アポを取る」項目が既に
// カレンダーに入っていないかを照らすために使う（docs/design.md「1件ずつの完了確認」）
const upcoming = upcomingRaw
  .filter((e) => e.kind === 'appointment')
  .map((e) => ({ ...e, ...(PREP_PATTERN.test(e.title) ? { prep: true } : {}) }));
const timed = events.filter((e) => !e.allDay).sort((a, b) => a.start.localeCompare(b.start));
const routine = routineOf(today);

/** 実予定が生活パターンの帯と重なっているものを拾う（通勤中の会議、夕食準備中の面談など） */
function routineConflicts(timed, bands) {
  const out = [];
  for (const e of timed) {
    if (e.kind !== 'appointment') continue;
    const s = minOf(e.start);
    const t = minOf(e.end);
    for (const b of bands) {
      if (b.s < t && b.t > s) {
        out.push({ event: `${e.start}-${e.end} ${e.title}`, band: `${b.from}-${b.to} ${b.label}`, kind: b.kind });
      }
    }
  }
  return out;
}

// 「やること」に載せる量の上限（docs/design.md「業務量」）。今日のまだ過ぎていない空き時間（睡眠以外。1日 dayMinutes まで）に、
// 2日目以降は1日 dayMinutes を足す。優先順がまだ粗いので、今日だけでなく days 日分を候補として並べる
const gaps = freeGaps(timed, routine);
const capacity = (() => {
  const live = gaps.filter((g) => !g.past).map((g) => ({ min: g.remainingMinutes ?? g.minutes, low: !!g.lowEnergy }));
  const free = live.reduce((n, g) => n + g.min, 0);
  const focus = live.filter((g) => !g.low).reduce((n, g) => n + g.min, 0);
  const { dayMinutes, days } = CFG.capacity;
  return {
    freeMinutes: free, focusMinutes: focus, longestMinutes: Math.max(0, ...live.map((g) => g.min)),
    dayMinutes, days, assignableMinutes: Math.min(free, dayMinutes) + dayMinutes * Math.max(0, days - 1),
  };
})();

const result = {
  generatedAt: new Date().toISOString(),
  today,
  note: '優先度は未計算。行順・マーカーの意味と優先度の軸はエンジンの docs/design.md 参照。連続する非空行が1項目か複数項目かは機械判定していないため lines を保持している',
  warnings,
  kyo: {
    docId: DOC_ID,
    capture,
    recent: {
      dateHeading: recent.dateHeading,
      startYmd: recent.startYmd,
      isToday: recent.isToday,
      sinkFound: recent.sinkFound,
      blocks: recent.blocks,
    },
  },
  notes,
  topPriority,
  keepInMind,
  ledgers: { thresholdDays: LEDGER_DAYS, scanned, staleSkipped, stale: staleList, active: ledgers.length, overdue, upcoming: dueSoon, aims, ballReturned: ballReturned.items, files: ledgers },
  gmail,
  sent: { days: SENT_DAYS, ...sent },
  mailFiles,
  waiting,
  reminders,
  routine: {
    weekday: routine.weekday,
    window: routine.window,
    workStart: routine.workStart,
    eveningLowEnergy: routine.eveningLowEnergy,
    bands: routine.bands.map(({ s, t, ...b }) => b),
    conflicts: routineConflicts(timed, routine.bands),
  },
  capacity,
  calendar: { events, timed, gaps, upcomingDays: UPCOMING_DAYS, upcoming, conflicts },
  attendanceNotices,
};

const json = JSON.stringify(result, null, 2);
if (STDOUT) {
  console.log(json);
} else {
  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, `${json}\n`);
  const kyoBlocks = capture.blocks.length + recent.blocks.length;
  const openItems = ledgers.reduce((n, l) => n + l.openCount, 0);
  console.log(`✅ ${relative(process.cwd(), OUT_PATH)}`);
  const todayBlocks = recent.blocks.filter((b) => b.date === today).length;
  // 見出しの幅をそろえる（全角は2桁と数える）
  const vpad = (t, w) => t + ' '.repeat(Math.max(1, w - [...t].reduce((n, c) => n + (c.charCodeAt(0) > 0xff ? 2 : 1), 0)));
  console.log(`   ${vpad(DOC, 12)}: 捕捉 ${capture.blocks.length} ブロック / 直近 ${recent.blocks.length} ブロック（計 ${kyoBlocks}）`);
  console.log(`   ${vpad(`${DOC} 起点`, 12)}: ${recent.dateHeading}${recent.isToday ? '' : '（当日の見出しが無いため直近へフォールバック）'} / 当日分 ${todayBlocks} ブロック`);
  if (CFG.features.appleNotes) console.log(`   Apple Notes : ${notes.found ? `${notes.lines.length} 行` : '未設置'}`);
  console.log(`   最優先課題  : ${topPriority.found ? `${topPriority.sections.length} 節` : '未読み取り'}`);
  console.log(`   業務量の上限: 約 ${Math.round(capacity.assignableMinutes / 6) / 10} 時間（${capacity.days} 日分。今日の残りの空き ${capacity.freeMinutes} 分＋1日 ${capacity.dayMinutes} 分×${capacity.days - 1}。今日の連続最大 ${capacity.longestMinutes} 分）。「やること」の見積の合計をこれに収める`);
  console.log(`   意識する案件: ${keepInMind.found ? `${keepInMind.items.length} 件（次の一手が無い ${keepInMind.items.filter((k) => k.ledgers.every((l) => !l.next)).length}・${KEEP_IDLE_DAYS}日以上止まっている ${keepInMind.items.filter((k) => k.ledgers.every((l) => l.idle)).length}）。「やること」の直下の節に優先順で出す` : 'なし'}`);
  console.log(`   台帳        : ${ledgers.length} ファイル / 未処理 ${openItems} 件（鮮度 ${LEDGER_DAYS} 日。過ぎていても期限・待ち・[!] のある ${ledgers.filter((l) => l.stale).length} ファイルは読み、${staleSkipped} ファイルを除外、走査 ${scanned}）`);
  console.log(`   期限超過    : ${overdue.length} 件（${new Set(overdue.map((o) => o.path)).size} ファイル）${overdue.length ? '。ledgers.overdue を「やること」の先頭に【期限超過】として出す' : ''}`);
  console.log(`   先の期限    : ${dueSoon.length} 件（${new Set(dueSoon.map((o) => o.path)).size} ファイル）。ledgers.upcoming を「直近の締切」「先の締切」に漏れなく出す`);
  console.log(`   返していない受信: ${ballReturned.checked ? `台帳の行 ${ballReturned.items.length} 件（台帳が引く Gmail の ID ${ballReturned.ids} 件のうち）。ledgers.ballReturned を today.md のどこかに載せる` : '未照会'}`);
  console.log(`   目安        : ${aims.length} 件（自分で決めた日付。期限として扱わず、ledgers.aims を優先順位の材料にだけ使う）`);
  console.log(`   Gmail inbox : ${gmail.found ? `${gmail.messages.length} 件（概算 ${gmail.resultSizeEstimate ?? '不明'}）` : '未取得'}`);
  console.log(`   送信済み    : ${sent.found ? `直近 ${SENT_DAYS} 日 ${sent.messages.length} 件${sent.messages.length >= SENT_MAX ? '（上限で切れている）' : ''}（予約送信を含む）。today.md の各項目を1件ずつ照らす` : '未取得'}`);
  for (const m of mailFiles) console.log(`   ${vpad(`${m.label} 受信`, 12)}: ${m.found ? `${m.inbox.messages.length} 通（受信トレイ ${m.inbox.total ?? '不明'} 通、未読 ${m.inbox.messages.filter((x) => x.unread).length}）/ 送信済み 直近 ${m.sent.days} 日 ${m.sent.messages.length} 通。mailFiles を Gmail と同じく照らす` : '未取得'}`);
  if (reminders.length) console.log(`   督促が要る  : ${reminders.length} 件（返事待ちが ${REMIND_DAYS} 日を超えた。docs/design.md「督促の扱い」。下書き済み ${reminders.filter((r) => r.draftIds.length).length} 件）`);
  const answered = waiting.items.filter((w) => w.replies?.length).length;
  console.log(`   返事待ち    : ${waiting.items.length} 件${waiting.checked ? ` / うち返事あり ${answered} 件` : '（Gmail 未照会）'}`);
  const usable = result.calendar.gaps.filter((g) => !g.past)
    .reduce((n, g) => n + (g.remainingMinutes ?? g.minutes), 0);
  console.log(`   カレンダー  : ${CALENDARS.map((c) => c.label).join('・') || 'なし'} / 予定 ${timed.length} 件 / 空き ${result.calendar.gaps.length} 枠 / 先 ${UPCOMING_DAYS} 日の予定 ${upcoming.length} 件（準備の要る予定 ${upcoming.filter((e) => e.prep).length} 件。台帳に準備の行があるか照らす）`);
  const ng = conflicts.items.filter((c) => c.level === 'ng').length;
  console.log(`   予定の重なり: 先 ${conflicts.days ?? '-'} 日 まずい ${ng} 件 / 確かめる ${conflicts.items.length - ng} 件 / 問題なし ${conflicts.okCount ?? 0} 件${ng ? '。today.md の「予定の重なり」に出す' : ''}`);
  for (const n of attendanceNotices) {
    const pending = n.items.filter((x) => !x.notified);
    console.log(`   ${vpad(n.label, 12)}: 先 ${n.days} 日の対象 ${n.targetDays.length} 日（祝日で除外 ${n.holidays.length} 日）/ 行けない・遅れる ${n.items.length} 日 / 連絡が未確認 ${pending.length} 日${n.mailChecked ? '' : '（送信済み未照会）'}${pending.length ? `。today.md の「やること」に${n.notifyName}への連絡として出す` : ''}`);
  }
  console.log(`   生活パターン: ${routine.weekday} / 帯 ${routine.bands.length} 件 / 予定との衝突 ${result.routine.conflicts.length} 件`);
  console.log(`   残り可処分  : ${usable} 分`);
}
