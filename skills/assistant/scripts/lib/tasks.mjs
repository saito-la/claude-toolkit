/**
 * Google Tasks の読み書き。外部の期日を予定表に「タスク」として載せるために使う（docs/design.md「期日を予定表に載せる」）。
 *
 * タスクは持ち主にしか見えず、共有・公開している予定表には出ない。予定（イベント）として入れると、
 * 予定表を見る相手に締切が見え、空き時間も塞ぐため、タスクにする。
 * 資格情報は設定の deadlineTasks.credential（スコープ https://www.googleapis.com/auth/tasks）。
 */
import { accessToken } from './google.mjs';

const API = 'https://tasks.googleapis.com/tasks/v1';
// このエンジンが作ったタスクの印。notes の行頭に置き、手で作ったタスクには触らない
export const KEY_PREFIX = 'assistant-key: ';

async function call(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = res.status === 204 ? {} : await res.json();
  if (!res.ok) throw new Error(`Google Tasks ${method} ${path} に失敗: ${JSON.stringify(data).slice(0, 300)}`);
  return data;
}

export const tasksToken = (credential) => accessToken(credential);

/** 題名でタスクリストを探す。無ければ create のとき作り、作らないなら null */
export async function findList(token, title, { create = false } = {}) {
  const { items = [] } = await call(token, 'GET', '/users/@me/lists?maxResults=100');
  const hit = items.find((l) => l.title === title);
  if (hit) return hit.id;
  return create ? (await call(token, 'POST', '/users/@me/lists', { title })).id : null;
}

/** リストのタスク（隠れた完了済みも含む）。dueMin・dueMax は YYYY-MM-DD */
export async function listTasks(token, listId, { dueMin, dueMax, showCompleted = true } = {}) {
  const out = [];
  let pageToken = '';
  do {
    const q = new URLSearchParams({ maxResults: '100', showCompleted: String(showCompleted), showHidden: String(showCompleted) });
    if (dueMin) q.set('dueMin', `${dueMin}T00:00:00.000Z`);
    if (dueMax) q.set('dueMax', `${dueMax}T00:00:00.000Z`);
    if (pageToken) q.set('pageToken', pageToken);
    const data = await call(token, 'GET', `/lists/${encodeURIComponent(listId)}/tasks?${q}`);
    out.push(...(data.items || []));
    pageToken = data.nextPageToken || '';
  } while (pageToken);
  return out;
}

export const insertTask = (token, listId, { title, notes, due }) =>
  call(token, 'POST', `/lists/${encodeURIComponent(listId)}/tasks`, { title, notes, due: `${due}T00:00:00.000Z` });

export const completeTask = (token, listId, id) =>
  call(token, 'PATCH', `/lists/${encodeURIComponent(listId)}/tasks/${encodeURIComponent(id)}`, { status: 'completed' });

/** タスクの期日（YYYY-MM-DD）。Tasks の due は日付だけが意味を持つ */
export const dueOf = (task) => (task.due ? task.due.slice(0, 10) : null);
export const keyOf = (task) => (task.notes || '').split('\n').find((l) => l.startsWith(KEY_PREFIX))?.slice(KEY_PREFIX.length) ?? null;
