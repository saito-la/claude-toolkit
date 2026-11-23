/**
 * 規則に当たらなかった受信メールを1通ずつ判断する。claude -p に扱いと一言を返させるだけで、道具は渡さない。
 * 扱いを実施するのは呼び出し側（mail-autopilot.mjs）。
 *
 * 扱いは3つ。最初の版では、判断でメールを動かさない（アーカイブは使い手が承認した規則だけが行う）。
 *   - archive：読むだけで済みそう。動かさず、同じ形の通をまとめて「規則にしてよいか」を使い手に問う
 *   - attention：使い手の返信・作業・判断が要る。受信トレイに残し、印「要対応」を付ける
 *   - keep：使い手が自分で読んで決めるのがよい（私信、本文だけでは決められない）。印「済」だけを付ける
 * 決めきれないときは unsure と問いを返させ、印「確認」を付けて使い手に問う。
 *
 * 最も重い誤りは、返信や着手が要るメールを archive にすること。プロンプトでもそう書く。
 */
import { readFileSync, existsSync } from 'fs';
import { runClaude } from './claude.mjs';

export const KINDS = { archive: 'アーカイブしてよさそう', attention: '対応が要る', keep: '本人が読んで決める' };

const SCHEMA = {
  type: 'object',
  properties: {
    kind: { type: 'string', enum: Object.keys(KINDS) },
    unsure: { type: 'boolean' },
    question: { type: 'string' },
    line: { type: 'string' },
    reason: { type: 'string' },
    sensitive: { type: 'boolean' },
    deadline: { type: ['string', 'null'] },
  },
  required: ['kind', 'unsure', 'question', 'line', 'reason', 'sensitive', 'deadline'],
};

/** 本文の冒頭。引用行と、過去のやりとりの引用が始まる行から後を落とす */
export function excerpt(body, max = 4000) {
  const lines = [];
  for (const l of String(body || '').split('\n')) {
    if (/^\s*>/.test(l)) continue;
    if (/^(-----Original Message-----|-----元のメッセージ-----|From: .+|差出人: .+|On .+wrote:|\d{4}年\d{1,2}月\d{1,2}日.+:)\s*$/.test(l.trim())) break;
    lines.push(l);
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, max);
}

/** 使い手が宛先のどこにいるか。self は使い手のアドレス（小文字）の列 */
export function selfPosition(msg, self = []) {
  if (!self.length) return null;
  const mine = new Set(self);
  const has = (h) => (String(h || '').toLowerCase().match(/[\w.+-]+@[\w.-]+/g) || []).some((a) => mine.has(a));
  if (has(msg.to)) return 'To に入っている';
  if (has(msg.cc)) return 'Cc だけ（To には入っていない）';
  return 'To にも Cc にも無い（メーリングリスト・Bcc・転送など）';
}

const jstText = (iso) => new Date(Date.parse(iso) + 9 * 3600_000).toISOString().slice(0, 16).replace('T', ' ');

/**
 * { msg（read の結果）, self, owner（使い手の説明）, guidanceFile, model, cwd } → { value, seconds, cost }
 */
export async function decide({ msg, self, owner, guidanceFile, model, cwd }) {
  const guidance = guidanceFile && existsSync(guidanceFile) ? readFileSync(guidanceFile, 'utf8').trim() : '';
  const pos = selfPosition(msg, self);
  const today = new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
  const mail = [
    `受信: ${jstText(msg.received)}`,
    `差出人: ${msg.from}`,
    `宛先: ${msg.to}`,
    ...(msg.cc ? [`Cc: ${msg.cc}`] : []),
    ...(pos ? [`使い手の位置: ${pos}`] : []),
    `件名: ${msg.subject}`,
    ...(msg.attachments?.length ? [`添付: ${msg.attachments.join(', ')}`] : []),
    '本文:',
    excerpt(msg.body),
  ].join('\n');

  const prompt = `あなたは、次の使い手の受信メールを1通ずつ仕分ける。今日は ${today}。

## 使い手

${owner || '（説明が設定されていない）'}

## 仕分け

下のメールを、次の3つのどれか1つに分ける。

- archive（${KINDS.archive}）：読むだけで済み、返信も作業も記録も要らないもの（お知らせ、システムの通知、広告、明細、メーリングリストや一斉配信のうち使い手宛の依頼を含まないもの）。この扱いでもメールは動かさない。同じ差出人・同じ件名の形のメールを、規則で自動にアーカイブしてよいかを使い手に問うのに使う
- attention（${KINDS.attention}）：使い手の返信・作業・判断・確認が要るもの。期限のある依頼、質問、確認や同意を求める依頼、日程の回答、使い手が後で参照すべき共有や報告
- keep（${KINDS.keep}）：機械は扱いを決めず、使い手が自分で読んで決めるのがよいもの（私的な連絡、家族、本文だけでは扱いを決められないもの）

最も重い誤りは、返信や着手が要るメールを archive にすること。迷ったら archive にしない。

決めきれないときは unsure を true にし、kind には傾いた扱いを入れる。迷いの典型は、使い手が Cc だけか To に並ぶ1人で、本文で担当が別の人に割り振られており、使い手の作業が要るかが本文から決まらないもの。一斉配信だが使い手宛の依頼を含むかもしれないもの。下の方針に似ているが同じ形とは言い切れないもの。本文から扱いが明らかなもの、方針で決まるものでは迷わない（問いは使い手の手間になる）。

返す項目：
- kind：上の3つのどれか
- unsure：決めきれないなら true
- question：unsure のとき、使い手への問いを1文で。何が決めきれないかと、傾いた扱いでよいかを尋ねる。false なら空文字
- line：差出人（人なら姓とさん、組織なら短い名前）と用件を30字以内で（例「事務部 会議室の予約変更のお知らせ」「田中さん 申請書の確認依頼」）
- reason：その扱いにした理由を1文で
- sensitive：人事（採用・評価・処遇・健康）か、研究対象者の個人の情報を含むなら true
- deadline：使い手が対応すべき期限を本文から読み取り、ISO 8601（+09:00）で書く。無ければ null。推測で作らない

${guidance ? `## 使い手の方針\n\n${guidance}\n\n` : ''}## メール

${mail}`;

  return runClaude({ ...model, prompt, schema: SCHEMA, cwd, label: `判断 ${msg.id}` });
}
