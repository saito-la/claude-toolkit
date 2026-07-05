#!/usr/bin/env node
/**
 * Google Docs 整形ツール（体裁プリセット適用）
 * Usage: node format-gdoc.mjs <docId> [--inspect] [--raw] [--account <name>] [--preset <name>]
 *
 * --inspect : 変更せず、先頭段落のスタイル（namedStyle/罫線/網掛け）と余白を表示
 * 通常      : プリセットに従い 余白・フォント・サイズ・行間・タイトル/サブタイトル・見出しレベルを一括適用
 * 既存の drive.file トークン(.gdrive-credentials-<account>-rw.json)を流用（既定account: default）。
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';

const CM_PER_PT = 28.3464567;

// 体裁プリセット。用途に応じて追加できる（`- name: {...}` の形で増やす）。
const PRESETS = {
  // 既定プリセット：余白1.5cm・全文メイリオ・タイトル中央/サブタイトル右詰め・行間0.85
  'formal-ja': {
    marginCm: 1.5,
    fontFamily: 'Meiryo',
    lineSpacing: 85,
    size: { TITLE: 14, SUBTITLE: 10, HEADING_1: 12, HEADING_2: 11, HEADING_3: 10, NORMAL_TEXT: 10 },
    spaceAbove: { TITLE: 0, SUBTITLE: 0, HEADING_1: 10, HEADING_2: 8, HEADING_3: 6, NORMAL_TEXT: 0 },
  },
};

const DOC_ID = process.argv[2];
const INSPECT = process.argv.includes('--inspect');
const accountIdx = process.argv.indexOf('--account');
const ACCOUNT = accountIdx !== -1 ? process.argv[accountIdx + 1] : 'default';
const presetIdx = process.argv.indexOf('--preset');
const PRESET_NAME = presetIdx !== -1 ? process.argv[presetIdx + 1] : 'formal-ja';
const PRESET = PRESETS[PRESET_NAME];

if (!DOC_ID) { console.error('Usage: node format-gdoc.mjs <docId> [--inspect] [--account <name>] [--preset <name>]'); process.exit(1); }
if (!PRESET) { console.error(`未知のpreset: ${PRESET_NAME}（利用可能: ${Object.keys(PRESETS).join(', ')}）`); process.exit(1); }

const CREDS = `${process.env.HOME}/.config/gdrive-mcp/.gdrive-credentials-${ACCOUNT}-rw.json`;
const OAUTH = `${process.env.HOME}/.config/gdrive-mcp/credentials.json`;
const MARGIN_PT = PRESET.marginCm * CM_PER_PT;

async function token() {
  const oauth = JSON.parse(readFileSync(OAUTH, 'utf8')).installed;
  let creds = JSON.parse(readFileSync(CREDS, 'utf8'));
  if (!creds.expiry_date || creds.expiry_date < Date.now() + 60000) {
    const p = new URLSearchParams({ client_id: oauth.client_id, client_secret: oauth.client_secret, refresh_token: creds.refresh_token, grant_type: 'refresh_token' });
    const r = await fetch('https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: p.toString() });
    const d = await r.json();
    if (d.error) throw new Error(`refresh failed: ${d.error} ${d.error_description || ''}`);
    creds.access_token = d.access_token; creds.expiry_date = Date.now() + d.expires_in * 1000;
    writeFileSync(CREDS, JSON.stringify(creds, null, 2));
  }
  return creds.access_token;
}

async function getDoc(tok) {
  const r = await fetch(`https://docs.googleapis.com/v1/documents/${DOC_ID}`, { headers: { Authorization: `Bearer ${tok}` } });
  const d = await r.json();
  if (!r.ok) throw new Error(`get failed: ${JSON.stringify(d)}`);
  return d;
}

async function batch(tok, requests) {
  const r = await fetch(`https://docs.googleapis.com/v1/documents/${DOC_ID}:batchUpdate`, {
    method: 'POST', headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ requests }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(`batchUpdate failed: ${JSON.stringify(d)}`);
  return d;
}

const tok = await token();
const doc = await getDoc(tok);
const content = doc.body.content;
const paras = content.filter(e => e.paragraph);

// diagnostics
console.log('=== 診断 ===');
const ds = doc.documentStyle || {};
console.log('余白(pt):', ['marginTop','marginBottom','marginLeft','marginRight'].map(k => `${k}=${ds[k]?.magnitude ?? '?'}`).join(' '));
paras.slice(0, 6).forEach((p, i) => {
  const ps = p.paragraph.paragraphStyle || {};
  const txt = (p.paragraph.elements || []).map(e => e.textRun?.content || '').join('').trim().slice(0, 24);
  const borders = ['borderTop','borderBottom','borderLeft','borderRight','borderBetween'].filter(b => ps[b] && (ps[b].width?.magnitude || ps[b].dashStyle));
  console.log(`  [${i}] ${ps.namedStyleType || '?'} | shading=${ps.shading?.backgroundColor ? 'Y' : 'n'} | borders=${borders.join(',') || 'none'} | "${txt}"`);
});

if (process.argv.includes('--raw')) {
  const raw = JSON.stringify(doc);
  const bm = (raw.match(/bookmark/gi) || []).length;
  console.log('\n=== raw scan ===');
  console.log('「bookmark」出現回数:', bm);
  console.log('doc top-level keys:', Object.keys(doc).join(','));
  const h = paras.find(p => (p.paragraph.paragraphStyle?.namedStyleType || '').startsWith('HEADING'));
  if (h) console.log('\n見出し段落の生JSON:\n', JSON.stringify(h.paragraph, null, 1).slice(0, 1500));
  const t = paras[0].paragraph.elements?.find(e => e.textRun);
  console.log('\nタイトルrunのtextStyle:', JSON.stringify(t?.textRun?.textStyle || {}));
  const b = paras[3]?.paragraph.elements?.find(e => e.textRun);
  console.log('本文runのtextStyle:', JSON.stringify(b?.textRun?.textStyle || {}));
  process.exit(0);
}

if (INSPECT) { console.log('\n(inspect only, no changes)'); process.exit(0); }

const isHeading = (p) => (p.paragraph.paragraphStyle?.namedStyleType || '').startsWith('HEADING');
const firstHeading = paras.findIndex((p, i) => i >= 1 && isHeading(p));
const end = firstHeading === -1 ? paras.length : firstHeading;

// 各段落の目標スタイル：先頭=タイトル／見出し前まで=サブタイトル／見出しは1段昇格／他=標準
const targetOf = (p, i) => {
  if (i === 0) return 'TITLE';
  if (i < end) return 'SUBTITLE';
  const t = p.paragraph.paragraphStyle?.namedStyleType;
  if (t === 'HEADING_2') return 'HEADING_1';
  if (t === 'HEADING_3') return 'HEADING_2';
  if (t === 'HEADING_4') return 'HEADING_3';
  if (t && t.startsWith('HEADING')) return t;
  return 'NORMAL_TEXT';
};
const alignOf = (t) => (t === 'TITLE' ? 'CENTER' : t === 'SUBTITLE' ? 'END' : 'START'); // タイトル中央・サブタイトル右詰め・他左

const requests = [
  { updateDocumentStyle: { documentStyle: {
      marginTop: { magnitude: MARGIN_PT, unit: 'PT' }, marginBottom: { magnitude: MARGIN_PT, unit: 'PT' },
      marginLeft: { magnitude: MARGIN_PT, unit: 'PT' }, marginRight: { magnitude: MARGIN_PT, unit: 'PT' },
    }, fields: 'marginTop,marginBottom,marginLeft,marginRight' } },
];
paras.forEach((p, i) => {
  const t = targetOf(p, i);
  requests.push({ updateParagraphStyle: { range: { startIndex: p.startIndex, endIndex: p.endIndex },
    paragraphStyle: { namedStyleType: t, alignment: alignOf(t),
      lineSpacing: PRESET.lineSpacing, spaceAbove: { magnitude: PRESET.spaceAbove[t] ?? 0, unit: 'PT' }, spaceBelow: { magnitude: 0, unit: 'PT' } },
    fields: 'namedStyleType,alignment,lineSpacing,spaceAbove,spaceBelow' } });
  const tsEnd = p.endIndex - 1; // 段落記号を除く
  if (tsEnd > p.startIndex) requests.push({ updateTextStyle: { range: { startIndex: p.startIndex, endIndex: tsEnd },
    textStyle: { fontSize: { magnitude: PRESET.size[t] || 10, unit: 'PT' }, weightedFontFamily: { fontFamily: PRESET.fontFamily }, bold: t.startsWith('HEADING') },
    fields: 'fontSize,weightedFontFamily,bold' } });
});

await batch(tok, requests);
console.log(`\n✅ 整形適用（preset: ${PRESET_NAME}）: 余白${PRESET.marginCm}cm / 全文${PRESET.fontFamily} / 罫線・網掛け除去 / タイトル・サブタイトル設定`);
console.log(`   https://docs.google.com/document/d/${DOC_ID}/edit`);
