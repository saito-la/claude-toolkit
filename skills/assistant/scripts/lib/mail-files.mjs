/**
 * ファイルのメール（設定の mailFiles）を読む。Gmail の API で読めないメールボックスを、別の道具が書き出した
 * ファイルで渡すためにある。最初の使い道は、組織の端末で
 * ブラウザの Outlook（OWA）の一覧から書き出したもの。ファイルの形はその出力：
 *   { generated, today, inbox: { total, messages }, sent: { days, messages } }
 *   messages の各通：id・from（送信済みは to）・address・subject・date・time・unread・flagged・pinned・replied・
 *   forwarded・attachment・important・draft・meeting・snippet・link
 *
 * 設定：{ "label": "Outlook", "path": "data/outlook-mail.json", "maxAgeHours": 24 }
 *   path はインスタンスのフォルダからの相対か、~/ で始まる絶対。maxAgeHours（既定 24）より古ければ警告する
 *
 * ファイルが無い・読めないときは found: false を返し、理由を warnings に入れる。書き出す道具が動かない端末でも、
 * 同じ設定のまま他の入力源だけで回せるようにするため。
 */
import { readFileSync } from 'fs';
import { isAbsolute } from 'path';
import { instance, expandHome } from './instance.mjs';

export function readMailFile(cfg, warnings) {
  const raw = expandHome(cfg.path || '');
  const path = isAbsolute(raw) ? raw : instance().file(raw);
  let data;
  try {
    data = JSON.parse(readFileSync(path, 'utf8'));
  } catch (e) {
    warnings.push(`${cfg.label} のメールのファイルを読めない（${path}）: ${String(e.message).slice(0, 100)}`);
    return { label: cfg.label, found: false };
  }
  const ageH = (Date.now() - Date.parse(data.generated)) / 3_600_000;
  if (!(ageH <= (cfg.maxAgeHours ?? 24))) warnings.push(`${cfg.label} のメールのファイルは ${Math.round(ageH)} 時間前のもの。書き出し直していない`);
  return { label: cfg.label, found: true, generated: data.generated, inbox: data.inbox, sent: data.sent };
}
