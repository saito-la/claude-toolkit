#!/usr/bin/env node
/**
 * アカウントと権限を引数で受ける汎用の Google OAuth 認証。トークンを資格情報のフォルダに保存する。
 *
 * 資格情報のフォルダと OAuth のクライアントは、インスタンスのフォルダで実行すれば assistant.json の
 * credentialDir・oauthClient、インスタンスの外で実行すれば既定値（lib/instance.mjs の DEFAULTS）。
 * OAuth のクライアント（Desktop 型）の作り方は README.md「資格情報」。
 *
 *   node auth-google.mjs <保存するファイル名> <権限...> [--hint <アドレス>] [--no-open]
 *
 *   例：node auth-google.mjs .google-work.json documents calendar.readonly gmail.readonly --hint taro@example.com
 *
 * 権限は https://www.googleapis.com/auth/ の後ろだけを書く（drive・spreadsheets・calendar.readonly 等）。
 * 1つのアカウントの権限は1つのトークンにまとめてよい。
 * 権限を足すときは、足した後の全部を渡して取り直す（上書きする）。
 *
 * --hint はログイン画面で選ぶアカウント。--no-open はブラウザを開かず URL だけを出す（別のプロファイルや
 * シークレットウィンドウで開くとき）。待ち受けのポートは空いているものを使う（OAuth クライアントは Desktop 型で、
 * ループバックの任意のポートを受け付ける）。失敗したらブラウザに理由とやり直しのリンクを出して待ち続け、15分で諦めて終わる。
 * どのアカウントで許可されたかを確かめるため、openid と email を常に足す。--hint と違うアカウントなら保存しない。
 * Windows でも動くよう、ホームは os.homedir() で取り、ブラウザは start で開く。
 */
import { createServer } from 'http';
import { readFileSync, writeFileSync } from 'fs';
import { exec } from 'child_process';
import { DEFAULTS, expandHome, instance } from './lib/instance.mjs';
import { join } from 'path';

const argv = process.argv.slice(2);
const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv.splice(i, 2)[1]; };
const flag = (name) => { const i = argv.indexOf(name); if (i === -1) return false; argv.splice(i, 1); return true; };
const hint = opt('--hint');
const noOpen = flag('--no-open');
const [outName, ...scopeNames] = argv;

if (!outName || scopeNames.length === 0 || outName.includes('/') || outName.includes('\\')) {
  console.error('usage: node auth-google.mjs <保存するファイル名> <権限...> [--hint <アドレス>] [--no-open]');
  process.exit(2);
}

let cfg = DEFAULTS;
try { cfg = instance().cfg; } catch { /* インスタンスの外：既定値 */ }
const BASE = expandHome(cfg.credentialDir);
const CREDS_OUT = join(BASE, outName);
const oauth = JSON.parse(readFileSync(join(BASE, cfg.oauthClient), 'utf8')).installed;
const scopes = scopeNames.map((s) => (s.startsWith('https://') ? s : `https://www.googleapis.com/auth/${s}`));

// 失敗したときはブラウザに理由とやり直しのリンクを出し、待ち受けを続ける。端末の画面を見ていない人にも分かるように
const fail = (res, msg) => {
  console.error(msg);
  res.end(`<meta charset="utf-8"><h2>保存していません</h2><p>${msg}</p><p><a href="${authUrl}">もう一度ログインする</a></p>`);
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url, redirectUri);
  if (url.pathname !== '/') { res.end(); return; }
  const code = url.searchParams.get('code');
  if (!code) return fail(res, `許可されませんでした（${url.searchParams.get('error') || 'code が無い'}）`);

  const t = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      code,
      client_id: oauth.client_id,
      client_secret: oauth.client_secret,
      redirect_uri: redirectUri,
      grant_type: 'authorization_code',
    }).toString(),
  }).then((r) => r.json());
  if (t.error || !t.refresh_token) return fail(res, `トークンの交換に失敗しました（${t.error || 'refresh_token が無い'}）`);

  // 同意画面のチェックを外されると、足りないまま保存されて後で 403 になる
  const granted = new Set(t.scope.split(' '));
  const missing = scopes.filter((s) => !granted.has(s));
  if (missing.length) return fail(res, `同意画面でチェックの入っていない権限がありました：${missing.join(' ')}。すべてにチェックを入れて許可してください`);

  // 複数のアカウントでログインしているブラウザでは、別のアカウントで許可してしまうことがある
  const who = await fetch('https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=' + t.access_token).then((r) => r.json());
  if (hint && who.email?.toLowerCase() !== hint.toLowerCase()) return fail(res, `${hint} ではなく ${who.email} で許可されました。${hint} を選んでください`);

  const creds = {
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    scope: t.scope,
    token_type: t.token_type,
    expiry_date: Date.now() + t.expires_in * 1000,
  };
  writeFileSync(CREDS_OUT, JSON.stringify(creds, null, 2), { mode: 0o600 });
  res.end(`<meta charset="utf-8"><h2>${who.email} の認証を保存しました。このタブを閉じてください。</h2>`);
  console.log('保存しました:', CREDS_OUT);
  console.log('アカウント:', who.email);
  console.log('権限:', t.scope);
  server.close();
  process.exit(0);
});

let redirectUri, authUrl;
server.listen(0, () => {
  redirectUri = `http://localhost:${server.address().port}`;
  authUrl = new URL('https://accounts.google.com/o/oauth2/auth');
  authUrl.searchParams.set('client_id', oauth.client_id);
  authUrl.searchParams.set('redirect_uri', redirectUri);
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('scope', [...scopes, 'openid', 'email'].join(' '));
  authUrl.searchParams.set('access_type', 'offline');
  authUrl.searchParams.set('prompt', 'consent');
  if (hint) authUrl.searchParams.set('login_hint', hint);

  console.log(`ブラウザで${hint ? ` ${hint} として` : ''}ログインし、許可してください。`);
  console.log('URL:', authUrl.toString());
  if (!noOpen) exec(process.platform === 'win32' ? `start "" "${authUrl}"` : `open "${authUrl}"`);
});

setTimeout(() => {
  console.error('15分待っても認証が来なかったので終わる');
  process.exit(1);
}, 15 * 60 * 1000).unref();
