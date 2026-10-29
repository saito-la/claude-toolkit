// Google auth + Gmail + Calendar REST helpers.
// Reuses the same OAuth token files and refresh flow as the gdrive-mcp wrappers
// (~/.config/gdrive-mcp/gmail-wrapper.mjs) — plain fetch, zero npm deps.
import { readFileSync, writeFileSync } from 'node:fs';

// --- OAuth client factory (one per credentials file) ---
export function makeClient({ credentialsPath, oauthClientPath }) {
  const readCreds = () => JSON.parse(readFileSync(credentialsPath, 'utf8'));

  async function refresh(force = false) {
    let creds, oauth;
    try {
      creds = readCreds();
      oauth = JSON.parse(readFileSync(oauthClientPath, 'utf8'));
    } catch { return; }
    if (!force && (!creds.expiry_date || creds.expiry_date > Date.now() + 300_000)) return;
    const clientId = oauth.installed?.client_id || oauth.web?.client_id;
    const clientSecret = oauth.installed?.client_secret || oauth.web?.client_secret;
    if (!clientId || !clientSecret || !creds.refresh_token) return;
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
    if (data.error) throw new Error(`token refresh failed: ${data.error} ${data.error_description || ''}`);
    writeFileSync(credentialsPath, JSON.stringify({
      access_token: data.access_token,
      refresh_token: creds.refresh_token,
      scope: data.scope || creds.scope,
      token_type: data.token_type || creds.token_type,
      expiry_date: Date.now() + data.expires_in * 1000,
    }, null, 2));
  }

  const token = () => readCreds().access_token;

  async function api(url, opts = {}) {
    await refresh(false);
    const doFetch = () => fetch(url, {
      ...opts,
      headers: {
        Authorization: `Bearer ${token()}`,
        ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
        ...opts.headers,
      },
    });
    let res = await doFetch();
    if (res.status === 401) { await refresh(true); res = await doFetch(); }
    const text = await res.text();
    const json = text ? JSON.parse(text) : {};
    if (!res.ok) throw new Error(`Google API ${res.status}: ${json.error?.message || text}`);
    return json;
  }

  const scopes = () => (readCreds().scope || '').split(/\s+/);
  return {
    api,
    hasScope: (s) => scopes().includes(s),
    scopes,
  };
}

// ===================== Gmail =====================
const GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me';

export function makeGmail(client) {
  const searchMessages = async (q, maxResults = 25) => {
    const u = new URL(`${GMAIL}/messages`);
    u.searchParams.set('q', q);
    u.searchParams.set('maxResults', String(maxResults));
    const r = await client.api(u.toString());
    return r.messages || [];
  };

  const getMessage = (id) =>
    client.api(`${GMAIL}/messages/${id}?format=full`);

  const modifyMessage = (id, { addLabelIds = [], removeLabelIds = [] }) =>
    client.api(`${GMAIL}/messages/${id}/modify`, {
      method: 'POST',
      body: JSON.stringify({ addLabelIds, removeLabelIds }),
    });

  const listLabels = async () => (await client.api(`${GMAIL}/labels`)).labels || [];

  const ensureLabel = async (name) => {
    const labels = await listLabels();
    const found = labels.find((l) => l.name === name);
    if (found) return found.id;
    const created = await client.api(`${GMAIL}/labels`, {
      method: 'POST',
      body: JSON.stringify({
        name,
        labelListVisibility: 'labelShow',
        messageListVisibility: 'show',
      }),
    });
    return created.id;
  };

  return { searchMessages, getMessage, modifyMessage, listLabels, ensureLabel };
}

// Decode a Gmail message: header lookups + concatenated text/html body.
export function parseMessage(msg) {
  const headers = {};
  for (const h of msg.payload?.headers || []) headers[h.name.toLowerCase()] = h.value;
  const decode = (data) =>
    data ? Buffer.from(data.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8') : '';
  let body = '';
  const walk = (part) => {
    if (!part) return;
    if (part.body?.data) body += '\n' + decode(part.body.data);
    for (const p of part.parts || []) walk(p);
  };
  walk(msg.payload);
  return {
    id: msg.id,
    threadId: msg.threadId,
    labelIds: msg.labelIds || [],
    from: headers.from || '',
    subject: headers.subject || '',
    snippet: msg.snippet || '',
    body,
  };
}

// ===================== Calendar =====================
const CAL = 'https://www.googleapis.com/calendar/v3';

export function makeCalendar(client) {
  // Returns busy intervals [{start,end}] merged across the given calendars.
  const freeBusy = async ({ calendarIds, timeMin, timeMax, timezone }) => {
    const r = await client.api(`${CAL}/freeBusy`, {
      method: 'POST',
      body: JSON.stringify({
        timeMin, timeMax, timeZone: timezone,
        items: calendarIds.map((id) => ({ id })),
      }),
    });
    const busy = [];
    for (const cal of Object.values(r.calendars || {})) {
      for (const b of cal.busy || []) busy.push({ start: new Date(b.start), end: new Date(b.end) });
    }
    return busy.sort((a, b) => a.start - b.start);
  };

  // All-day events (start.date, not start.dateTime) in the window, across calendars.
  // Used as a freeBusy fallback: an all-day event with transparency=transparent
  // (Google Calendar's default for all-day events) is silently absent from
  // freeBusy — this recovers events whose title looks like an absence anyway.
  const listAllDayEvents = async ({ calendarIds, timeMin, timeMax }) => {
    const events = [];
    for (const id of calendarIds) {
      const u = new URL(`${CAL}/calendars/${encodeURIComponent(id)}/events`);
      u.searchParams.set('timeMin', timeMin);
      u.searchParams.set('timeMax', timeMax);
      u.searchParams.set('singleEvents', 'true');
      const r = await client.api(u.toString());
      for (const ev of r.items || []) {
        if (ev.status === 'cancelled' || !ev.start?.date) continue;
        events.push({ summary: ev.summary || '', description: ev.description || '', start: ev.start.date, end: ev.end.date, transparency: ev.transparency || 'opaque' });
      }
    }
    return events;
  };

  return { freeBusy, listAllDayEvents };
}
