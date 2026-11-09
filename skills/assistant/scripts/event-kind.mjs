/**
 * カレンダーのイベント名から「予定」かどうかを判定する。正本。
 *
 * collect.mjs（可処分時間の計算）と doc-2w.mjs（作業用の文書への予定転記）が同じ判定を
 * 必要とするため、ここに1つだけ置く。両方に列挙を持たせるとズレる。
 *
 *   selfBlock  … 他人に予約を入れさせないための自衛ブロック（「不在」）。
 *                その帯が実際に埋まっているかは生活パターン（routine.json）が決める
 *   workplace  … 予定ではなく勤務場所の記載（「本部」「名古屋 (オフィス)」のような拠点の名前）。
 *                空きだが場所が変わる
 *   appointment… 実予定。時間を塞ぐ
 *
 * 勤務場所は「<拠点> (オフィス)」の形で増えるため、
 * 拠点名を列挙し続けるのをやめて接尾辞で拾う。全角括弧・括弧内外の空白も同じ形とみなす。
 * この接尾辞を持たない勤務場所の書き方は、インスタンスの assistant.json の workplaceTitles に完全一致で挙げる。
 */
import { instance } from './lib/instance.mjs';

const SELF_BLOCK_TITLES = ['不在'];
const workplaceTitles = () => instance().cfg.workplaceTitles;
const WORKPLACE_RE = /[（(]\s*オフィス\s*[）)]$/;

export function classifyEventTitle(title) {
  const t = (title || '').trim();
  if (SELF_BLOCK_TITLES.includes(t)) return 'selfBlock';
  if (workplaceTitles().includes(t) || WORKPLACE_RE.test(t)) return 'workplace';
  return 'appointment';
}
