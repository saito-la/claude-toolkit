/**
 * メールの層の Gmail の実装。mail-autopilot.mjs はこの形の操作だけを使い、Outlook の実装（outlook.mjs）と差し替えられる。
 *
 *   listUnmarked({ sinceDays, limit })  受信トレイの、印の無い通（古い順）。{ id, conversation, from, to, cc, subject, received, unread }
 *   read(id)                            上に本文 body と添付の名前 attachments を足したもの
 *   mark(id, name)                      印を付ける。Gmail ではラベル。無ければ作る
 *   archive(id)                         受信トレイから外す（未読のまま）
 *   self()                              使い手の送信用アドレス（小文字）
 *   link(id)                            人が開くための URL（無ければ null）
 *
 * 印の名前は設定の mailAutopilot.marks の値。読み取りの資格情報（gmail.readonly）でも試運転は回る。
 * 本番では gmail.modify と gmail.labels が要る。
 */
import { accessToken } from '../lib/google.mjs';

const API = 'https://gmail.googleapis.com/gmail/v1/users/me';

export async function openGmail({ credential, account, marks }) {
  const token = await accessToken(credential);
  const call = async (path, { method = 'GET', body, query } = {}) => {
    const url = new URL(`${API}/${path}`);
    for (const [k, v] of Object.entries(query || {})) (Array.isArray(v) ? v : [v]).forEach((x) => url.searchParams.append(k, x));
    const r = await fetch(url, { method, headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    const data = r.status === 204 ? {} : await r.json();
    if (!r.ok) throw new Error(`Gmail ${method} ${path} に失敗（${r.status}）: ${JSON.stringify(data).slice(0, 300)}`);
    return data;
  };

  let labelMap = null; // 名前 → ID
  const labels = async () => {
    if (labelMap) return labelMap;
    labelMap = new Map(((await call('labels')).labels || []).map((l) => [l.name, l.id]));
    return labelMap;
  };
  const markIds = async () => {
    const m = await labels();
    return new Set(Object.values(marks).map((n) => m.get(n)).filter(Boolean));
  };

  const header = (msg, n) => (msg.payload?.headers || []).find((h) => h.name.toLowerCase() === n.toLowerCase())?.value || '';
  const meta = (msg) => ({
    id: msg.id,
    conversation: msg.threadId,
    from: header(msg, 'From'),
    to: header(msg, 'To'),
    cc: header(msg, 'Cc'),
    subject: header(msg, 'Subject'),
    received: new Date(Number(msg.internalDate)).toISOString(),
    unread: (msg.labelIds || []).includes('UNREAD'),
    labelIds: msg.labelIds || [],
  });

  let mine = null;
  const self = async () => (mine ??= [...new Set([account, ...((await call('settings/sendAs')).sendAs || []).map((a) => a.sendAsEmail)].filter(Boolean).map((a) => a.toLowerCase()))]);

  return {
    name: 'gmail',
    self,
    link: (id) => `https://mail.google.com/mail/u/${encodeURIComponent(account || 0)}/#all/${id}`,

    async listUnmarked({ sinceDays = 14, limit = 20 } = {}) {
      const ids = [];
      let pageToken;
      do {
        const r = await call('messages', { query: { q: `in:inbox newer_than:${sinceDays}d`, maxResults: 100, ...(pageToken ? { pageToken } : {}) } });
        ids.push(...(r.messages || []).map((m) => m.id));
        pageToken = r.nextPageToken;
      } while (pageToken && ids.length < 1000);
      const skip = await markIds();
      const me = await self();
      const out = [];
      for (const id of ids) {
        const m = await call(`messages/${id}`, { query: { format: 'metadata', metadataHeaders: ['From', 'To', 'Cc', 'Subject'] } });
        if ((m.labelIds || []).some((l) => skip.has(l) || l === 'DRAFT')) continue;
        const x = meta(m);
        if (me.some((a) => x.from.toLowerCase().includes(a))) continue; // 使い手自身が送った通（スレッドの中の送信）は相手の受信ではない
        out.push(x);
      }
      out.sort((a, b) => a.received.localeCompare(b.received));
      return out.slice(0, limit);
    },

    async read(id) {
      const m = await call(`messages/${id}`, { query: { format: 'full' } });
      return { ...meta(m), body: textOf(m.payload), attachments: attachmentsOf(m.payload) };
    },

    async mark(id, name) {
      const m = await labels();
      if (!m.has(name)) {
        const l = await call('labels', { method: 'POST', body: { name, labelListVisibility: 'labelShow', messageListVisibility: 'show' } });
        m.set(name, l.id);
      }
      await call(`messages/${id}/modify`, { method: 'POST', body: { addLabelIds: [m.get(name)] } });
    },

    async archive(id) {
      await call(`messages/${id}/modify`, { method: 'POST', body: { removeLabelIds: ['INBOX'] } });
    },
  };
}

const b64 = (s) => Buffer.from(String(s || '').replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');

/** 本文の文字。text/plain を優先し、無ければ text/html から文字だけを取る */
export function textOf(payload) {
  const plain = [];
  const html = [];
  const walk = (p) => {
    if (!p) return;
    if (p.filename) return; // 添付
    if (p.mimeType === 'text/plain' && p.body?.data) plain.push(b64(p.body.data));
    else if (p.mimeType === 'text/html' && p.body?.data) html.push(b64(p.body.data));
    (p.parts || []).forEach(walk);
  };
  walk(payload);
  if (plain.length) return plain.join('\n').replace(/\r\n/g, '\n');
  return htmlToText(html.join('\n'));
}

export function htmlToText(h) {
  return String(h || '')
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n').replace(/<\/(p|div|tr|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

function attachmentsOf(payload) {
  const out = [];
  const walk = (p) => {
    if (!p) return;
    if (p.filename) out.push(p.filename);
    (p.parts || []).forEach(walk);
  };
  walk(payload);
  return out;
}
