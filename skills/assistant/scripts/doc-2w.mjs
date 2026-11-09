#!/usr/bin/env node
/**
 * doc-2w.mjs — 予定表（設定の calendars）の今後2週間の予定を、作業用の文書（設定の workDoc）へ転記する
 *
 * 予定表が複数あれば1つの並びにまとめ、同じ時刻・同じ題名の予定は1行にする。どの予定表から来たかは行に書かない。
 * 予定の正本は予定表で、作業用の文書の行は表示にすぎない（docs/design.md「予定の正本」）。
 *
 * 「# 2w Schedule」見出しの直下に、日付降順（新しい日が上）で
 *   2026/7/28 Tue        ← 見出し1（書式の正本は lib/date-heading.mjs）
 *   8:30-9:00 部会Mtg  ← 本文
 * の形式で挿入する。
 *
 * Usage:
 *   node doc-2w.mjs                  # dry-run（何を入れるか表示するだけ）。インスタンスのフォルダで実行する
 *   node doc-2w.mjs --apply          # 実際に作業用の文書へ挿入
 *   node doc-2w.mjs --days 21        # 期間を変える（既定 14 日）
 *   node doc-2w.mjs --from 2026-08-01 --apply
 *
 * 挙動：
 *   - 既定の起点は「明日」。--from で変更できる。
 *   - 予定でないイベント（自衛ブロック「不在」・勤務場所の記載）は転記しない。判定は event-kind.mjs。
 *   - 自分が「欠席」と回答した予定は転記しない。
 *   - 既に同じ日付の見出しが「# 2w Schedule」セクションにある場合は、
 *     見出しを重複させず、その見出しの直下に不足分の行だけを足す。
 *   - 既に同じ「時刻 タイトル」の行がある場合は足さない（=何度実行しても増えない）。
 *   - 消さないので、一部の予定表しか読めない端末から流しても他の予定を壊さない。転記した後に変わった予定や
 *     取り消された予定は残る。
 *   - 「# 2w Schedule」配下の日付見出しが旧書式（2026.7.28 Tue など）なら、新しい書式へ書き換える。
 *
 * 外部の期日（設定の deadlineTasks のタスクリストにある未完了のタスク。deadline-tasks.mjs が作る）は、
 * 「締切 〇〇」の行としてその日の先頭（終日の予定より上）に入れる（docs/design.md「期日を予定表に載せる」）。
 *
 * 認証は設定の calendars[].credential（読み取り）と workDoc.credential（書き込み）。type が "file" の予定表は
 * 認証せずにファイルを読む（lib/calendars.mjs）。
 * 切れた場合は scripts/auth-google.mjs で取り直す（README.md「資格情報」）。
 */
import { classifyEventTitle } from './event-kind.mjs';
import { formatDateHeading, ymdOfDateHeading } from './lib/date-heading.mjs';
import { instance, need } from './lib/instance.mjs';
import { accessToken, getDoc as fetchDoc, batchUpdate as update, paragraphs } from './lib/google.mjs';
import { fileEvents, isFileCalendar } from './lib/calendars.mjs';
import { tasksToken, findList, listTasks, dueOf } from './lib/tasks.mjs';

// ── 設定 ───────────────────────────────────────────────
const CFG = instance().cfg;
const DOC_ID = need('workDoc.id');
const DOC_CRED = need('workDoc.credential');
const CALENDARS = need('calendars');
const ANCHOR_HEADING = '# 2w Schedule'; // この見出しの直下に入れる
const TZ = 'Asia/Tokyo';
// 転記しないイベント（自衛ブロック・勤務場所の記載）。判定の正本は event-kind.mjs。
// 判定を広げれば、既に文書にある行も次の実行で消える（findStaleLines）
const DEFAULT_DAYS = 14;

// ── 引数 ───────────────────────────────────────────────
const argv = process.argv.slice(2);
const APPLY = argv.includes('--apply');
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i !== -1 && argv[i + 1] ? argv[i + 1] : fallback;
};
const DAYS = parseInt(arg('--days', String(DEFAULT_DAYS)), 10);

// ── 日付ユーティリティ（すべて JST 前提）─────────────────
// JST の壁時計での「今日」を YYYY-MM-DD で返す
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
// 年ゆれを吸収して突き合わせるためのキー："2026/7/28 Tue" → "7.28"
function mdKeyOfHeading(text) {
  const ymd = ymdOfDateHeading(text);
  return ymd ? mdKeyOfYmd(ymd) : null;
}
function mdKeyOfYmd(ymd) {
  const [, m, d] = ymd.split('-').map(Number);
  return `${m}.${d}`;
}
// RFC3339 → JST の "H:MM"（12時間制。13:00→1:00。am/pmは付けない＝前後の文脈で自明とする）
function hhmm(iso) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: false,
  }).formatToParts(new Date(iso));
  const h24 = Number(parts.find((p) => p.type === 'hour').value);
  const min = parts.find((p) => p.type === 'minute').value;
  const h12 = h24 % 12 || 12;
  return `${h12}:${min}`;
}
// RFC3339 → JST の "YYYY-MM-DD"
function ymdOf(iso) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(iso));
}

// ── カレンダー取得 ──────────────────────────────────────
// 1つの予定表の予定。読めない予定表があれば止まる（足りない行を足すだけなので、次に読めたときに足せばよい）。
// ファイルの予定表は、書き出す道具が動かない端末もあるので、読めなければ警告して飛ばす（lib/calendars.mjs）
async function fetchEvents(cal, from, to) {
  if (isFileCalendar(cal)) {
    const w = [];
    const items = fileEvents(cal, from, to, w);
    w.forEach((m) => console.log(`⚠️  ${m}`));
    return items || [];
  }
  const { credential, calendarId = 'primary' } = cal;
  const token = await accessToken(credential);
  const q = new URLSearchParams({
    timeMin: `${from}T00:00:00+09:00`,
    timeMax: `${to}T00:00:00+09:00`,
    singleEvents: 'true',
    orderBy: 'startTime',
    maxResults: '250',
    timeZone: TZ,
  });
  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events?${q}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const data = await res.json();
  if (!res.ok) throw new Error(`カレンダー取得に失敗: ${JSON.stringify(data).slice(0, 300)}`);
  return data.items || [];
}

// イベント → { ymd, line } の配列（除外・整形込み）
function toLines(events) {
  const out = [];
  for (const ev of events) {
    if (ev.status === 'cancelled') continue;
    const title = (ev.summary || '(無題)').trim();
    if (classifyEventTitle(title) !== 'appointment') continue;
    const me = (ev.attendees || []).find((a) => a.self);
    if (me && me.responseStatus === 'declined') continue;

    if (ev.start.date) {
      // 終日予定：またがる日すべてに置く（end.date は排他的）
      for (let d = ev.start.date; d < ev.end.date; d = addDays(d, 1)) {
        out.push({ ymd: d, sort: -1, line: `終日 ${title}` });
      }
    } else {
      const ymd = ymdOf(ev.start.dateTime);
      const s = hhmm(ev.start.dateTime);
      const e = hhmm(ev.end.dateTime);
      out.push({ ymd, sort: new Date(ev.start.dateTime).getTime(), line: `${s}-${e} ${title}` });
    }
  }
  return out;
}

// 外部の期日のタスク → { ymd, line }。日の先頭に置くため sort は終日（-1）より前にする
async function deadlineLines(from, to) {
  const dt = CFG.deadlineTasks;
  if (!dt?.credential) return [];
  const token = await tasksToken(dt.credential);
  const listId = await findList(token, dt.list);
  if (!listId) return [];
  return (await listTasks(token, listId, { dueMin: from, dueMax: to, showCompleted: false }))
    .filter((t) => t.status !== 'completed' && dueOf(t) >= from && dueOf(t) < to)
    .map((t) => ({ ymd: dueOf(t), sort: -2, line: `締切 ${t.title.trim()}` }));
}

// ── Docs ───────────────────────────────────────────────
const getDoc = (token) => fetchDoc(DOC_ID, token);
const batchUpdate = (token, requests) => update(DOC_ID, token, requests);
const paragraphsOf = paragraphs;

/**
 * 「# 2w Schedule」セクションを読み、
 *   sectionStart: 見出し直後のインデックス
 *   days: [{ mdKey, headingText, headingStart, headingEnd, blockEnd, bodyLines:[…] }]（文書順＝日付降順）
 * を返す。blockEnd はその日のブロックの終わり（＝次の見出しの開始位置）で、
 * 「その日の次に古い日」を挿し込む位置になる。
 */
function parseSection(paras) {
  const ai = paras.findIndex((p) => p.style === 'HEADING_1' && p.text.trim() === ANCHOR_HEADING);
  if (ai === -1) throw new Error(`「${ANCHOR_HEADING}」という見出し1が見つかりません`);
  const sectionStart = paras[ai].end;

  const days = [];
  for (let i = ai + 1; i < paras.length; i++) {
    const p = paras[i];
    if (p.style !== 'HEADING_1') continue;
    const key = mdKeyOfHeading(p.text);
    if (!key) break; // 日付でない見出し1（= 別セクションの開始）でセクション終了
    // この日付見出しに属する本文行を集める
    const bodyLines = [];
    let j = i + 1;
    for (; j < paras.length && paras[j].style !== 'HEADING_1'; j++) {
      const t = paras[j].text.trim();
      if (t) bodyLines.push({ text: t, start: paras[j].start, end: paras[j].end });
    }
    const blockEnd = j < paras.length ? paras[j].start : p.end;
    days.push({ mdKey: key, headingText: p.text, headingStart: p.start, headingEnd: p.end, blockEnd, bodyLines });
  }
  return { sectionStart, days };
}

/**
 * 旧書式の日付見出しを拾い、新しい書式の文字列を添えて返す（後ろから書き換える順）。
 * 見出しの段落は残し、文字だけを入れ替えるので、見出し1のままになる
 */
function findOldHeadings(section) {
  return section.days
    .map((d) => ({ ...d, to: formatDateHeading(ymdOfDateHeading(d.headingText)) }))
    .filter((d) => d.headingText.trim() !== d.to)
    .sort((a, b) => b.headingStart - a.headingStart);
}

/**
 * 転記済みの行のうち、いま除外対象になっているものを拾う。
 * 除外の判定を後から広げたとき、既に入っている行を消せるようにするため。
 * 「8:30-9:00 タイトル」「8:30 タイトル」「終日 タイトル」の形の行だけを対象にし、
 * 手書きのメモ行は触らない。
 */
function findStaleLines(section) {
  const stale = [];
  for (const day of section.days) {
    for (const b of day.bodyLines) {
      const m = b.text.match(/^(?:\d{1,2}:\d{2}(?:-\d{1,2}:\d{2})?|終日)\s+(.+)$/);
      if (m && classifyEventTitle(m[1]) !== 'appointment') {
        stale.push({ ...b, heading: day.headingText });
      }
    }
  }
  return stale.sort((a, b) => b.start - a.start); // 後ろから消す
}

/**
 * 24時間表記（2026-08-01 以前の転記分）で残っている行を拾う。
 * 削除後に再挿入させることで、新しい12時間表記（hhmm()）へ更新する。
 * 対象は今回の転記期間（targetDaysDesc）の見出しに限る。
 */
function findFormatStaleLines(section, targetDaysDesc) {
  const targetKeys = new Set(targetDaysDesc.map(mdKeyOfYmd));
  const to12 = (h) => Number(h) % 12 || 12;
  const stale = [];
  for (const day of section.days) {
    if (!targetKeys.has(day.mdKey)) continue;
    for (const b of day.bodyLines) {
      const m = b.text.match(/^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s(.+)$/);
      if (!m) continue;
      const [, h1, mi1, h2, mi2, title] = m;
      const converted = `${to12(h1)}:${mi1}-${to12(h2)}:${mi2} ${title}`;
      if (converted !== b.text) stale.push({ ...b, heading: day.headingText });
    }
  }
  return stale.sort((a, b) => b.start - a.start); // 後ろから消す
}

// ── 挿入プラン組み立て ──────────────────────────────────
/**
 * 挿入は「本文の後ろ側から前へ」適用するため、index 降順に並べて返す。
 * 各要素： { index, text, headingRanges:[[relS,relE]], bodyRanges:[[relS,relE]], label, order }
 * order は同一 index のときの適用順（0 を先に適用＝結果として後ろに来る）。
 */
function buildPlan(section, byDay, targetDaysDesc) {
  const existing = new Map(section.days.map((d) => [d.mdKey, d]));
  const inserts = [];

  // (a) 既存見出しがある日 → byDay（真の時刻順）を基準に、既存行をアンカーとして
  //     欠けている行をその間の正しい位置へ足す。全部を見出し直後に固定すると、
  //     既存の午前の行より新規の午後の行が文書上で先に来て時刻順が崩れるため
  //     （実例：2026.8.7 Fri。2026-08-01 指摘）。
  for (const ymd of targetDaysDesc) {
    const ex = existing.get(mdKeyOfYmd(ymd));
    if (!ex) continue;
    const target = byDay.get(ymd) || [];
    const haveMap = new Map(ex.bodyLines.map((b) => [b.text, b]));
    let anchor = ex.headingEnd;
    let group = [];
    const flush = () => {
      if (!group.length) return;
      let text = '';
      const bodyRanges = [];
      for (const l of group) {
        bodyRanges.push([text.length, text.length + l.length + 1]);
        text += `${l}\n`;
      }
      inserts.push({
        index: anchor,
        text,
        headingRanges: [],
        bodyRanges,
        label: `${ex.headingText}（既存見出しへ ${group.length} 行追加）`,
        order: 1,
      });
      group = [];
    };
    for (const l of target) {
      const hit = haveMap.get(l);
      if (hit) {
        flush();
        anchor = hit.end;
      } else {
        group.push(l);
      }
    }
    flush();
  }

  // (b) 見出しがない日 → 見出し＋本文を、日付降順が崩れない位置へ挿し込む。
  //     降順に走査し、直近に通過した既存見出しブロックの末尾をアンカーにする
  //     （まだ既存見出しを通過していなければセクション先頭）。
  let anchor = section.sectionStart;
  let group = [];
  const flush = () => {
    if (!group.length) return;
    let text = '';
    const headingRanges = [];
    const bodyRanges = [];
    const labels = [];
    for (const ymd of group) {
      const h = formatDateHeading(ymd);
      const lines = byDay.get(ymd) || [];
      headingRanges.push([text.length, text.length + h.length + 1]);
      text += `${h}\n`;
      for (const l of lines) {
        bodyRanges.push([text.length, text.length + l.length + 1]);
        text += `${l}\n`;
      }
      labels.push(`${h}（${lines.length} 件）`);
    }
    inserts.push({ index: anchor, text, headingRanges, bodyRanges, label: labels.join(' / '), order: 0 });
    group = [];
  };
  for (const ymd of targetDaysDesc) {
    const ex = existing.get(mdKeyOfYmd(ymd));
    if (ex) {
      flush();
      anchor = ex.blockEnd;
    } else {
      group.push(ymd);
    }
  }
  flush();

  // 後ろから適用（前方の挿入で後方のインデックスがずれるのを避ける）
  inserts.sort((a, b) => b.index - a.index || a.order - b.order);
  return inserts;
}

function toRequests(inserts) {
  const reqs = [];
  for (const ins of inserts) {
    reqs.push({ insertText: { location: { index: ins.index }, text: ins.text } });
    for (const [s, e] of ins.headingRanges) {
      reqs.push({
        updateParagraphStyle: {
          range: { startIndex: ins.index + s, endIndex: ins.index + e },
          paragraphStyle: { namedStyleType: 'HEADING_1' },
          fields: 'namedStyleType',
        },
      });
    }
    for (const [s, e] of ins.bodyRanges) {
      const range = { startIndex: ins.index + s, endIndex: ins.index + e };
      reqs.push({
        updateParagraphStyle: {
          range,
          paragraphStyle: { namedStyleType: 'NORMAL_TEXT' },
          fields: 'namedStyleType',
        },
      });
      // 直前の文字からリンク色・下線を引き継がないようにする
      reqs.push({
        updateTextStyle: {
          range,
          textStyle: {},
          fields: 'link,underline,foregroundColor',
        },
      });
    }
  }
  return reqs;
}

// ── メイン ─────────────────────────────────────────────
try {
  const from = arg('--from', addDays(todayJst(), 1));
  const to = addDays(from, DAYS);

  if (!CALENDARS.length) throw new Error('予定表の設定（calendars）が空');
  const events = (await Promise.all(CALENDARS.map((c) => fetchEvents(c, from, to)))).flat();
  // タスクを読めなくても予定の転記は止めない（Tasks API の無効・認証切れ）
  const deadlines = await deadlineLines(from, to).catch((e) => { console.log(`⚠️  締切のタスクを読めず、締切の行を入れない: ${e.message.slice(0, 160)}`); return []; });
  const lines = [...deadlines, ...toLines(events)];

  const byDay = new Map();
  for (const { ymd, sort, line } of lines) {
    if (!byDay.has(ymd)) byDay.set(ymd, []);
    const day = byDay.get(ymd);
    // カレンダーに同じ予定が二重登録されていることがある（招待の重複、複数の予定表に同じ予定）。
    // 作業用の文書は人が読む写しなので、同じ「時刻 タイトル」は1行にまとめる。
    if (day.some((x) => x.line === line)) continue;
    day.push({ sort, line });
  }
  for (const [k, v] of byDay) {
    v.sort((a, b) => a.sort - b.sort);
    byDay.set(k, v.map((x) => x.line));
  }

  // 期間内の全日（土日・予定のない日も見出しを作る）を降順で
  const targetDaysDesc = [];
  for (let d = from; d < to; d = addDays(d, 1)) targetDaysDesc.push(d);
  targetDaysDesc.reverse();

  console.log(`📅 対象期間: ${from} 〜 ${addDays(to, -1)}（${DAYS}日）／予定表: ${CALENDARS.map((c) => c.label).join('・')}`);
  console.log(`   取得 ${events.length} 件 → 転記対象 ${lines.length} 件（うち締切 ${deadlines.length} 件。除外: 自衛ブロック・勤務場所の記載／欠席回答）\n`);

  const token = await accessToken(DOC_CRED);
  const doc = await getDoc(token);
  let section = parseSection(paragraphsOf(doc));

  console.log(`📄 ${doc.title} → 「${ANCHOR_HEADING}」直下（既存の日付見出し ${section.days.length} 件）\n`);

  // 除外リストに該当する転記済みの行（＝あとから除外に足したもの）＋24時間表記の旧行（12時間表記へ更新）
  const excludeStale = findStaleLines(section);
  const formatStale = findFormatStaleLines(section, targetDaysDesc);
  const stale = [...excludeStale, ...formatStale].sort((a, b) => b.start - a.start);
  const oldHeadings = findOldHeadings(section);
  for (const h of oldHeadings) console.log(`  ~ ${h.headingText.trim()} → ${h.to}`);
  for (const s of excludeStale) console.log(`  − ${s.heading}: ${s.text}`);
  for (const s of formatStale) console.log(`  ± ${s.heading}: ${s.text}（12時間表記へ更新）`);

  let inserts = buildPlan(section, byDay, targetDaysDesc);
  for (const ins of [...inserts].reverse()) {
    console.log(`  + ${ins.label}`);
    for (const l of ins.text.split('\n').filter(Boolean)) console.log(`      ${l}`);
  }

  if (!stale.length && !inserts.length && !oldHeadings.length) {
    console.log('✅ 変更はありません（すべて転記済み）。');
    process.exit(0);
  }
  if (!APPLY) {
    console.log('\n（dry-run。実際に書き込むには --apply を付けてください）');
    process.exit(0);
  }

  // 削除と書き換えでインデックスが動くため、削除・書き換え → 取り直し → 挿入 の順に分けて適用する。
  // 削除する行と書き換える見出しは重ならないので、まとめて後ろから適用する
  if (stale.length || oldHeadings.length) {
    const edits = [
      ...stale.map((s) => ({ at: s.start, reqs: [{ deleteContentRange: { range: { startIndex: s.start, endIndex: s.end } } }] })),
      ...oldHeadings.map((h) => ({ at: h.headingStart, reqs: [
        { deleteContentRange: { range: { startIndex: h.headingStart, endIndex: h.headingEnd - 1 } } },
        { insertText: { location: { index: h.headingStart }, text: h.to } },
      ] })),
    ].sort((a, b) => b.at - a.at);
    await batchUpdate(token, edits.flatMap((e) => e.reqs));
    section = parseSection(paragraphsOf(await getDoc(token)));
    inserts = buildPlan(section, byDay, targetDaysDesc);
  }
  if (inserts.length) await batchUpdate(token, toRequests(inserts));

  console.log(`\n✅ ${CFG.workDoc.name} を更新しました（見出しの書き換え ${oldHeadings.length} / 削除 ${stale.length} 行 / 追加 ${inserts.reduce((n, i) => n + i.text.split('\n').filter(Boolean).length, 0)} 行）`);
  console.log(`   https://docs.google.com/document/d/${DOC_ID}/edit`);
} catch (err) {
  console.error('❌ エラー:', err.message);
  process.exit(1);
}
