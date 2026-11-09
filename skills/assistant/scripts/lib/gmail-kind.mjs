/**
 * Gmail の1通が、使い手の通か、送った・予約した・下書き・届いたのどれかを決める。判定の正本はここだけに置き、
 * evidence.mjs と check-today.mjs はこれを使う（2つに別々に書いていて、下書きの除外が片方にしか無かった。
 * 2026-10-29、未送信の下書きを「送った」と判定した）。
 *
 * 渡す通は Gmail API の messages・threads の metadata 形式（labelIds・internalDate・payload.headers の From）。
 *   - 下書き：DRAFT のラベルがある。差出人は使い手で、internalDate は作成・保存の時刻なので、先に見ないと「送った」になる。
 *     未送信なので、送った証拠にはならない
 *   - 予約した：SENT が無く、差出人が使い手で、日時が未来。予約送信は送られるまで SENT も SCHEDULED も付かず
 *     （API では CATEGORY_PERSONAL だけ）、日時は送る予定の時刻で記録される
 *   - 送った：SENT がある、または差出人が使い手
 *   - 届いた：それ以外
 */

export const header = (m, n) => (m.payload?.headers || []).find((h) => h.name === n)?.value || '';

/** 使い手の送信用アドレス（小文字）。get は Gmail API の users/me からの相対パスを受けて JSON（失敗なら null）を返す */
export async function myAddresses(get) {
  return ((await get('settings/sendAs'))?.sendAs || []).map((a) => a.sendAsEmail.toLowerCase());
}

const labels = (m) => m.labelIds || [];

export const isDraft = (m) => labels(m).includes('DRAFT');

/** 使い手の通か（下書きを含む） */
export const isMine = (m, myAddrs) => labels(m).includes('SENT') || myAddrs.some((a) => header(m, 'From').toLowerCase().includes(a));

/** '下書き'・'予約した'・'送った'・'届いた' のいずれか */
export function kindOf(m, myAddrs, now = Date.now()) {
  if (isDraft(m)) return '下書き';
  if (!isMine(m, myAddrs)) return '届いた';
  // 予約の時刻を過ぎて1分以内で、まだ SENT が付いていない通も予約として扱う
  if (!labels(m).includes('SENT') && Number(m.internalDate) > now - 60_000) return '予約した';
  return '送った';
}
