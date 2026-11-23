/**
 * Google Chat の Webhook へ1通送る（メールの自動処理の問い・失敗の知らせ）。
 *
 * Webhook の URL は、設定の mailAutopilot.chat.webhookEnv が指すファイルの CHAT_WEBHOOK_URL に置く（KEY=値 の形。値の引用符は
 * あってもなくてもよい）。URL は秘密なので、画面とログに出さない。試運転では送らずに文面を返すだけにする。
 */
import { readFileSync, existsSync } from 'fs';
import { expandHome } from '../lib/instance.mjs';

function webhookUrl(envFile) {
  const f = expandHome(envFile);
  if (!f || !existsSync(f)) throw new Error(`Chat の Webhook の設定ファイルが無い：${envFile}`);
  for (const line of readFileSync(f, 'utf8').split(/\r?\n/)) {
    const m = /^\s*CHAT_WEBHOOK_URL\s*=\s*"?([^"\s]+)"?\s*$/.exec(line);
    if (m) return m[1];
  }
  throw new Error(`${envFile} に CHAT_WEBHOOK_URL が無い`);
}

/** Chat の文面の中のリンク。文字に < > | があると崩れるので除く */
export const link = (url, text) => (url ? `<${url}|${String(text).replace(/[<>|]/g, ' ').trim()}>` : String(text));

export async function postChat(envFile, text) {
  const r = await fetch(webhookUrl(envFile), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=UTF-8' },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) throw new Error(`Chat への送信に失敗（${r.status}）`);
}
