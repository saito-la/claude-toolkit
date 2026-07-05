#!/usr/bin/env node
/**
 * docx/markdown → Google Docs アップローダー
 * Usage: node upload-gdoc.mjs <file> <folder-id> [--account <name>]
 *
 * アカウントごとに独立した drive.file スコープの認証情報を使う
 * （既定アカウント名: default）。初回はアカウントごとにブラウザで認証が必要。
 * 認証済み後は refresh_token で自動更新。
 * 認証情報: ~/.config/gdrive-mcp/.gdrive-credentials-<account>-rw.json
 * OAuthクライアント: ~/.config/gdrive-mcp/credentials.json（自分のGoogle CloudプロジェクトでOAuthクライアントを作成し配置する）
 */
import { createServer } from 'http';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { exec } from 'child_process';

const args = process.argv.slice(2).filter((a, i, arr) => {
  if (a === '--account') return false;
  if (arr[i - 1] === '--account') return false;
  return true;
});
const accountIdx = process.argv.indexOf('--account');
const ACCOUNT = accountIdx !== -1 ? process.argv[accountIdx + 1] : 'default';
const MARKDOWN_PATH = args[0];
const FOLDER_ID = args[1];

if (!MARKDOWN_PATH || !FOLDER_ID) {
  console.error('Usage: node upload-gdoc.mjs <file> <folder-id> [--account <name>]');
  process.exit(1);
}

const CREDS_OUT = `${process.env.HOME}/.config/gdrive-mcp/.gdrive-credentials-${ACCOUNT}-rw.json`;
const OAUTH_PATH = `${process.env.HOME}/.config/gdrive-mcp/credentials.json`;

const oauth = JSON.parse(readFileSync(OAUTH_PATH, 'utf8'));
const clientId = oauth.installed.client_id;
const clientSecret = oauth.installed.client_secret;
const redirectUri = 'http://localhost:4567';
const scope = 'https://www.googleapis.com/auth/drive.file';

async function refreshToken(creds) {
  const params = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: creds.refresh_token,
    grant_type: 'refresh_token',
  });
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });
  const data = await res.json();
  if (data.error) throw new Error(`Token refresh failed: ${data.error} - ${data.error_description}`);
  creds.access_token = data.access_token;
  creds.expiry_date = Date.now() + data.expires_in * 1000;
  writeFileSync(CREDS_OUT, JSON.stringify(creds, null, 2));
  return creds;
}

async function getAccessToken() {
  if (existsSync(CREDS_OUT)) {
    let creds = JSON.parse(readFileSync(CREDS_OUT, 'utf8'));
    if (creds.expiry_date < Date.now() + 60000) {
      console.log('🔄 トークンをリフレッシュ中...');
      creds = await refreshToken(creds);
    }
    return creds.access_token;
  }
  // 新規認証
  return new Promise((resolve, reject) => {
    const authUrl =
      `https://accounts.google.com/o/oauth2/auth` +
      `?client_id=${clientId}` +
      `&redirect_uri=${encodeURIComponent(redirectUri)}` +
      `&response_type=code` +
      `&scope=${encodeURIComponent(scope)}` +
      `&access_type=offline` +
      `&prompt=consent`;

    console.log(`\n=== Google Drive 書き込み認証（account: ${ACCOUNT}） ===`);
    console.log('ブラウザが開きます。アップロードに使いたいGoogleアカウントでログインしてください。\n');
    exec(`open "${authUrl}"`);

    const server = createServer(async (req, res) => {
      const url = new URL(req.url, 'http://localhost:4567');
      const code = url.searchParams.get('code');
      if (!code) { res.end('No code.'); return; }
      res.end('<h2>認証完了！このタブを閉じてください。</h2>');
      server.close();

      const params = new URLSearchParams({
        code, client_id: clientId, client_secret: clientSecret,
        redirect_uri: redirectUri, grant_type: 'authorization_code',
      });
      const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params.toString(),
      });
      const tokenData = await tokenRes.json();
      if (tokenData.error) { reject(new Error(tokenData.error)); return; }

      const creds = {
        access_token: tokenData.access_token,
        refresh_token: tokenData.refresh_token,
        scope: tokenData.scope,
        token_type: tokenData.token_type,
        expiry_date: Date.now() + tokenData.expires_in * 1000,
      };
      writeFileSync(CREDS_OUT, JSON.stringify(creds, null, 2));
      console.log('✅ 認証成功！');
      resolve(creds.access_token);
    });
    server.listen(4567, () => {
      console.log('ローカルサーバー起動中 (port 4567)... ブラウザでの認証を待っています。');
    });
  });
}

async function uploadAsGoogleDoc(accessToken, markdownPath, folderId) {
  const filename = markdownPath.split('/').pop().replace(/\.(md|docx)$/, '');

  // docx → Google Docs 変換アップロード
  const content = readFileSync(markdownPath);
  const boundary = '-------314159265358979323846';
  const metadata = JSON.stringify({
    name: filename,
    mimeType: 'application/vnd.google-apps.document',
    parents: [folderId],
  });

  const metaPart =
    `--${boundary}\r\n` +
    `Content-Type: application/json; charset=UTF-8\r\n\r\n` +
    `${metadata}\r\n`;
  const filePart =
    `--${boundary}\r\n` +
    `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n`;
  const closing = `\r\n--${boundary}--`;

  const body = Buffer.concat([
    Buffer.from(metaPart, 'utf8'),
    Buffer.from(filePart, 'utf8'),
    content,
    Buffer.from(closing, 'utf8'),
  ]);

  const res = await fetch(
    'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': `multipart/related; boundary="${boundary}"`,
      },
      body,
    }
  );

  const data = await res.json();
  if (!res.ok) {
    throw new Error(`Upload failed: ${JSON.stringify(data)}`);
  }
  return data;
}

// Main
try {
  console.log(`\n📄 アップロード対象: ${MARKDOWN_PATH}`);
  console.log(`📁 フォルダID: ${FOLDER_ID}\n`);

  const accessToken = await getAccessToken();
  const result = await uploadAsGoogleDoc(accessToken, MARKDOWN_PATH, FOLDER_ID);

  console.log(`\n✅ アップロード完了！`);
  console.log(`   ファイル名: ${result.name}`);
  console.log(`   ファイルID: ${result.id}`);
  console.log(`   URL: https://docs.google.com/document/d/${result.id}/edit`);
} catch (err) {
  console.error('❌ エラー:', err.message);
  process.exit(1);
}
