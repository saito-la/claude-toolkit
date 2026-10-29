// Decide whether an email is a schedule-input request we should auto-answer,
// which tool it uses, and whether it must be excluded.
//
// KEY: judge only the MAIN (top, unquoted) message — not quoted history. A minutes/
// notification/reply email often quotes an old thread that contained a poll URL; the
// answer URL must appear in the email's own main message, not in the quoted part.

const TOOLS = [
  { name: 'tonton',   re: /https?:\/\/tonton\.amaneku\.com\/list\.php\?id=([A-Za-z0-9_]+)/i },
  { name: 'chousei',  re: /https?:\/\/chouseisan\.com\/s\?h=([0-9a-f]+)/i },
  { name: 'yotei',    re: /https?:\/\/yotei-kun\.jp\/(?:s\/|#event\/)([A-Za-z0-9]+)/i },
];

// The mail must ask us to *enter* our availability, not merely announce a result.
const REQUEST_KEYWORDS = [
  '入力', 'ご都合', 'ご予定', '御予定', '日程調整', '調整', '回答', '出欠',
  '都合', '候補', 'スケジュール', 'ご記入', '記入をお願い', '入れてください',
];
// Phrases that mark a pure notification / result / minutes share (never auto-answer),
// checked against the MAIN message only.
const NOTIFICATION_ONLY = [
  '日程が確定', '日程が決定', '開催が決定', '確定しました', '決定しました',
  '結果のお知らせ', '締め切りました', '締切ました',
  '議事録', '議事次第', '議題登録', '報告いたします', '作成いたしました',
];

// Cut the body at the first quote/forward boundary and return the main (top) message.
function mainMessage(body) {
  const lines = (body || '').split(/\r?\n/);
  // A line that starts quoted history / a forwarded-or-reply header block.
  const boundary = new RegExp(
    '^\\s*(' +
      '>' +                                                   // quoted line
      '|\\*?\\s*(差出人|送信者|送信元|From)\\s*[:：]' +          // Outlook sender header (starts quote block)
      '|-{2,}\\s*(Original Message|原文|転送|Forwarded)' +      // "----- Original Message -----" 等
      '|_{5,}$' +                                             // underscore separator
      '|On\\s.+\\swrote:\\s*$' +                              // Gmail English quote header
      '|\\d{4}年\\d{1,2}月\\d{1,2}日.*[:：]\\s*$' +             // Gmail JP quote header "2026年…: "
    ')', 'i');
  let cut = lines.length;
  for (let i = 0; i < lines.length; i++) {
    if (boundary.test(lines[i].trim())) { cut = i; break; }
  }
  return lines.slice(0, cut).join('\n');
}

function extractEmail(fromHeader) {
  const m = fromHeader.match(/<([^>]+)>/);
  return (m ? m[1] : fromHeader).trim().toLowerCase();
}

// A NOTIFICATION_ONLY keyword doesn't mark the mail as a pure notification if it
// only appears inside a concessive clause ("議事録は追って共有しますが、〜") that
// hands off to a request later in the same sentence. Look at the keyword's own
// sentence (up to the next 。/\n) and require it NOT end in a concessive connector.
function isConcessiveMention(main, keyword) {
  const idx = main.indexOf(keyword);
  if (idx === -1) return false;
  const rest = main.slice(idx);
  const end = rest.search(/[。\n]/);
  const sentence = end === -1 ? rest : rest.slice(0, end);
  return /(ます|でし?)?(が|ものの|けれども)[、,]\s*$/.test(sentence);
}

export function classify(mail, cfg) {
  const main = `${mail.subject}\n${mainMessage(mail.body)}`;

  // 1) tool + answer URL — must be in the MAIN message (not quoted history)
  let tool = null, url = null, pollId = null;
  for (const t of TOOLS) {
    const m = main.match(t.re);
    if (m) { tool = t.name; url = m[0]; pollId = m[1]; break; }
  }
  if (!tool) {
    // If a URL exists only in the quoted part, say so; else no URL at all.
    const inQuote = TOOLS.some((t) => t.re.test(mail.body || ''));
    return { matched: false, reason: inQuote
      ? '日程調整ツールのURLは引用（過去スレッド）内のみ＝メインは日程調整依頼でない'
      : 'no tonton/調整さん/よていくん answer URL' };
  }

  // 2) exclusions (checked before "matched" so we can log the skip reason)
  const sender = extractEmail(mail.from);
  const exSenders = (cfg.exclude?.senders || []).map((s) => s.toLowerCase());
  if (exSenders.includes(sender)) {
    return { matched: false, excluded: true, tool, url, pollId,
      excludeReason: `除外リスト（config.exclude.senders）に一致する送信者のためスキップ (${sender})` };
  }
  for (const pat of cfg.exclude?.subjectPatterns || []) {
    if (new RegExp(pat, 'i').test(mail.subject)) {
      return { matched: false, excluded: true, tool, url, pollId,
        excludeReason: `除外件名パターン（config.exclude.subjectPatterns）「${pat}」に一致（該当メールは手動入力）` };
    }
  }

  // 3) main message must be an input request, not a notification/minutes
  const notificationHit = NOTIFICATION_ONLY.find((k) => main.includes(k) && !isConcessiveMention(main, k));
  if (notificationHit) {
    return { matched: false, tool, url, pollId, reason: `通知・議事録・結果共有と判定（メインは入力依頼でない：「${notificationHit}」）` };
  }
  const hasRequest = REQUEST_KEYWORDS.some((k) => main.includes(k));
  if (!hasRequest) {
    return { matched: false, tool, url, pollId, reason: '入力依頼のキーワードなし（共有のみと判断）' };
  }

  return { matched: true, tool, url, pollId, sender };
}
