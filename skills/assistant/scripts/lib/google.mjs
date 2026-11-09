/**
 * Google の API を叩くための共通部分：アクセストークンと Docs の読み書き。
 *
 * 資格情報は設定の credentialDir（既定 ~/.config/gdrive-mcp）に置いたファイルを名前で指す。
 * OAuth のクライアントは同じフォルダの oauthClient（既定 credentials.json）。取り方は
 * scripts/auth-google.mjs（README.md「資格情報」）。切れたら取り直す。
 */
import { readFileSync, writeFileSync } from 'fs';
import { instance } from './instance.mjs';

let oauth = null;
const client = () => (oauth ??= JSON.parse(readFileSync(instance().cred(instance().cfg.oauthClient), 'utf8')).installed);

/** 資格情報のファイル名（credentialDir からの相対）からアクセストークンを得る。期限が近ければ更新して書き戻す */
export async function accessToken(credName) {
  const credPath = instance().cred(credName);
  const creds = JSON.parse(readFileSync(credPath, 'utf8'));
  if (creds.expiry_date && creds.expiry_date > Date.now() + 60_000) return creds.access_token;
  const { client_id, client_secret } = client();
  const params = new URLSearchParams({ client_id, client_secret, refresh_token: creds.refresh_token, grant_type: 'refresh_token' });
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });
  const data = await res.json();
  if (data.error) throw new Error(`トークン更新に失敗（${credPath}）: ${data.error_description || data.error}`);
  creds.access_token = data.access_token;
  creds.expiry_date = Date.now() + data.expires_in * 1000;
  writeFileSync(credPath, JSON.stringify(creds, null, 2));
  return creds.access_token;
}

export async function getDoc(docId, token) {
  const res = await fetch(`https://docs.googleapis.com/v1/documents/${docId}?includeTabsContent=true`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`documents.get に失敗: ${JSON.stringify(data).slice(0, 300)}`);
  return data;
}

export async function batchUpdate(docId, token, requests) {
  const res = await fetch(`https://docs.googleapis.com/v1/documents/${docId}:batchUpdate`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ requests }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`batchUpdate に失敗: ${JSON.stringify(data).slice(0, 400)}`);
  return data;
}

/**
 * 先頭タブの本文段落を平坦化する。text は末尾の改行だけを落とした本文（前後の空白は残す）。
 * style は namedStyleType、h1 は見出し1、heading は見出しのいずれか
 */
export function paragraphs(doc) {
  const body = doc.tabs ? doc.tabs[0].documentTab.body : doc.body;
  const out = [];
  for (const el of body.content || []) {
    if (!el.paragraph) continue;
    const style = (el.paragraph.paragraphStyle || {}).namedStyleType || 'NORMAL_TEXT';
    out.push({
      start: el.startIndex,
      end: el.endIndex,
      style,
      h1: style === 'HEADING_1',
      heading: style.startsWith('HEADING'),
      text: (el.paragraph.elements || []).map((e) => (e.textRun ? e.textRun.content : '')).join('').replace(/\n$/, ''),
    });
  }
  return out;
}
