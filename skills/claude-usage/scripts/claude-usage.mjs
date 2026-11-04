#!/usr/bin/env node
// Claude Code の使用状況を、セッション記録（~/.claude/projects/**/*.jsonl）から集計する。
// 使い方と設計は同じスキルの SKILL.md が正本。
//
//   node claude-usage.mjs export  --machine <端末名> --out <DIR> [--idle-min 5]
//   node claude-usage.mjs summary --in <DIR> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--daily-out <CSV>]
//
// export は手元の記録から <DIR>/<端末名>-sessions.csv と <DIR>/<端末名>-spans.csv を書く（BOM 付き UTF-8）。
// 既にファイルがあれば、今回の走査に無いセッションの行は残す（記録が消えた後も前回までの集計を失わない）。
// summary は <DIR>/*-spans.csv を読み、端末ごとと全端末の集計を出す。
// 依存は node の標準モジュールだけ（Windows の Git Bash でも動かすため）。

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BOM = "﻿";

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.startsWith("--")) {
      const k = t.slice(2);
      const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
      a[k] = v;
    } else a._.push(t);
  }
  return a;
}

function die(msg) {
  process.stderr.write(msg + "\n");
  process.exit(2);
}

// ---------- 時刻 ----------
function pad(n, w = 2) {
  return String(n).padStart(w, "0");
}
function localIso(ms) {
  const d = new Date(ms);
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const ao = Math.abs(off);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${sign}${pad(Math.floor(ao / 60))}:${pad(ao % 60)}`
  );
}
function localDate(ms) {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function nextLocalMidnight(ms) {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
}

// ---------- CSV ----------
function csvCell(v) {
  const s = v === undefined || v === null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function writeCsv(file, header, rows) {
  const lines = [header.join(",")];
  for (const r of rows) lines.push(header.map((h) => csvCell(r[h])).join(","));
  const tmp = file + ".tmp";
  fs.writeFileSync(tmp, BOM + lines.join("\r\n") + "\r\n", "utf8");
  fs.renameSync(tmp, file);
}
function readCsv(file) {
  if (!fs.existsSync(file)) return [];
  let text = fs.readFileSync(file, "utf8");
  if (text.startsWith(BOM)) text = text.slice(1);
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else q = false;
      } else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  if (!rows.length) return [];
  const header = rows[0];
  return rows.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

// ---------- 記録の走査 ----------
function claudeRoot() {
  return process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
}
function listJsonl(dir) {
  const out = [];
  const walk = (d) => {
    let ents;
    try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile() && e.name.endsWith(".jsonl")) out.push(p);
    }
  };
  walk(dir);
  return out;
}
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function sessionIdFromPath(p) {
  const base = path.basename(p, ".jsonl");
  if (UUID_RE.test(base)) return base;
  // <セッションID>/subagents/agent-xxx.jsonl の形
  const parts = p.split(path.sep);
  const i = parts.lastIndexOf("subagents");
  if (i > 0 && UUID_RE.test(parts[i - 1])) return parts[i - 1];
  return base;
}
function categoryOf(entrypoint) {
  if (entrypoint === "cli") return "cli";
  if (entrypoint === "claude-vscode") return "vscode";
  return "other";
}
function lastPathPart(p) {
  if (!p) return "";
  const parts = String(p).split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] || "";
}
function isHumanPrompt(r) {
  if (r.type !== "user" || r.isSidechain || r.isMeta || r.isCompactSummary) return false;
  if (r.turnOrigin && r.turnOrigin !== "human") return false;
  const c = r.message && r.message.content;
  let text = "";
  if (typeof c === "string") text = c;
  else if (Array.isArray(c)) {
    if (c.some((b) => b && b.type === "tool_result")) return false;
    text = c.filter((b) => b && b.type === "text").map((b) => b.text || "").join("");
    if (!text && !c.some((b) => b && b.type === "image")) return false;
  } else return false;
  const t = text.trimStart();
  if (t.startsWith("<local-command-") || t.startsWith("[Request interrupted")) return false;
  return true;
}

function scan(idleMs) {
  const files = listJsonl(path.join(claudeRoot(), "projects"));
  const seenUuid = new Set();
  const seenMsg = new Set();
  const S = new Map(); // sessionId -> 集計
  const get = (sid) => {
    let s = S.get(sid);
    if (!s) {
      s = { sid, cwd: "", ep: new Map(), version: "", models: new Set(), events: [], prompts: [], usage: [], subMsgs: 0 };
      S.set(sid, s);
    }
    return s;
  };
  for (const f of files) {
    let text;
    try { text = fs.readFileSync(f, "utf8"); } catch { continue; }
    const fileSid = sessionIdFromPath(f);
    const inSub = f.split(path.sep).includes("subagents");
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      let r;
      try { r = JSON.parse(line); } catch { continue; }
      if (!r || typeof r !== "object") continue;
      if (r.uuid) {
        if (seenUuid.has(r.uuid)) continue; // 再開時に写された履歴を二重に数えない
        seenUuid.add(r.uuid);
      }
      const sid = inSub ? fileSid : r.sessionId || r.session_id || fileSid;
      const s = get(sid);
      const ts = r.timestamp ? Date.parse(r.timestamp) : NaN;
      if (!Number.isNaN(ts)) s.events.push([ts, r.entrypoint || ""]);
      if (r.cwd && !s.cwd) s.cwd = r.cwd;
      if (r.entrypoint) s.ep.set(r.entrypoint, (s.ep.get(r.entrypoint) || 0) + 1);
      if (r.version) s.version = r.version;
      if (r.type === "assistant" && r.message) {
        const m = r.message;
        if (m.model && m.model !== "<synthetic>") s.models.add(m.model);
        if (inSub || r.isSidechain) s.subMsgs++;
        const key = m.id || r.requestId || r.uuid;
        if (m.usage && key && !seenMsg.has(key)) {
          seenMsg.add(key);
          const u = m.usage;
          s.usage.push({
            ts,
            input: u.input_tokens || 0,
            output: u.output_tokens || 0,
            cacheCreate: u.cache_creation_input_tokens || 0,
            cacheRead: u.cache_read_input_tokens || 0,
          });
        }
      }
      if (!inSub && isHumanPrompt(r) && !Number.isNaN(ts)) s.prompts.push(ts);
    }
  }

  const sessions = [];
  const spans = [];
  for (const s of S.values()) {
    if (!s.events.length) continue;
    s.events.sort((a, b) => a[0] - b[0]);
    let entrypoint = "unknown", best = -1;
    for (const [k, v] of s.ep) if (v > best) { best = v; entrypoint = k; }
    const cat = categoryOf(entrypoint);
    // 間隔が idle 以下の記録をつないだ区間を「動いていた時間」とする。
    // 同じセッションを VS Code と CLI の両方で開くことがあるので、区間の種別はその区間の記録の多数で決める
    const sp = [];
    let cur = null;
    for (const [t, ep] of s.events) {
      if (cur && t - cur.b <= idleMs) cur.b = t;
      else { cur = { a: t, b: t, ep: new Map() }; sp.push(cur); }
      if (ep) cur.ep.set(ep, (cur.ep.get(ep) || 0) + 1);
    }
    const live = sp.filter((x) => x.b > x.a);
    const spanRows = live.map((x) => {
      let e = entrypoint, n = -1;
      for (const [k, v] of x.ep) if (v > n) { n = v; e = k; }
      return { a: x.a, b: x.b, cat: categoryOf(e), prompts: 0, input: 0, output: 0, cacheCreate: 0, cacheRead: 0 };
    });
    const find = (t) => {
      // 区間の外の記録は、直前の区間（無ければ最初の区間）に寄せる
      let k = -1;
      for (let i = 0; i < spanRows.length; i++) if (spanRows[i].a <= t) k = i;
      return k < 0 ? 0 : k;
    };
    let tot = { input: 0, output: 0, cacheCreate: 0, cacheRead: 0 };
    for (const u of s.usage) {
      for (const k of Object.keys(tot)) tot[k] += u[k];
      if (spanRows.length && !Number.isNaN(u.ts)) {
        const r = spanRows[find(u.ts)];
        for (const k of Object.keys(tot)) r[k] += u[k];
      }
    }
    for (const t of s.prompts) if (spanRows.length) spanRows[find(t)].prompts++;
    const activeMs = live.reduce((x, y) => x + (y.b - y.a), 0);
    sessions.push({
      session_id: s.sid,
      project: lastPathPart(s.cwd),
      entrypoint,
      category: cat,
      version: s.version,
      first: localIso(s.events[0][0]),
      last: localIso(s.events[s.events.length - 1][0]),
      active_min: (activeMs / 60000).toFixed(1),
      prompts: s.prompts.length,
      subagent_msgs: s.subMsgs,
      input_tokens: tot.input,
      output_tokens: tot.output,
      cache_creation_tokens: tot.cacheCreate,
      cache_read_tokens: tot.cacheRead,
      models: [...s.models].sort().join(";"),
    });
    for (const r of spanRows) {
      spans.push({
        session_id: s.sid,
        category: r.cat,
        start: localIso(r.a),
        end: localIso(r.b),
        prompts: r.prompts,
        input_tokens: r.input,
        output_tokens: r.output,
        cache_creation_tokens: r.cacheCreate,
        cache_read_tokens: r.cacheRead,
      });
    }
  }
  return { files: files.length, sessions, spans };
}

const SESSION_COLS = [
  "machine", "session_id", "project", "entrypoint", "category", "version", "first", "last", "active_min",
  "prompts", "subagent_msgs", "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "models",
];
const SPAN_COLS = [
  "machine", "session_id", "category", "start", "end", "prompts",
  "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
];

function cmdExport(a) {
  const machine = a.machine && a.machine !== true ? String(a.machine) : os.hostname().split(".")[0];
  if (!a.out || a.out === true) die("--out <DIR> が要る");
  const idleMin = a["idle-min"] ? Number(a["idle-min"]) : 5;
  if (!(idleMin > 0)) die("--idle-min は正の数");
  fs.mkdirSync(a.out, { recursive: true });
  const res = scan(idleMin * 60000);
  const sFile = path.join(a.out, `${machine}-sessions.csv`);
  const pFile = path.join(a.out, `${machine}-spans.csv`);
  const now = new Set(res.sessions.map((s) => s.session_id));
  const keptS = readCsv(sFile).filter((r) => !now.has(r.session_id));
  const keptP = readCsv(pFile).filter((r) => !now.has(r.session_id));
  const sessions = [...keptS, ...res.sessions.map((s) => ({ machine, ...s }))].sort((x, y) => (x.first < y.first ? -1 : 1));
  const spans = [...keptP, ...res.spans.map((s) => ({ machine, ...s }))].sort((x, y) => (x.start < y.start ? -1 : 1));
  writeCsv(sFile, SESSION_COLS, sessions);
  writeCsv(pFile, SPAN_COLS, spans);
  const firsts = sessions.map((s) => s.first).filter(Boolean);
  process.stdout.write(
    `端末 ${machine}: 記録 ${res.files} ファイルを走査、今回 ${res.sessions.length} セッション` +
      `（前回からの持ち越し ${keptS.length}）。期間 ${firsts[0] ? firsts[0].slice(0, 10) : "-"} ～ ` +
      `${firsts.length ? firsts[firsts.length - 1].slice(0, 10) : "-"}\n${sFile}\n${pFile}\n`
  );
}

// ---------- summary ----------
// 区間の集合から、日ごとに「1つ以上動いていた時間」「2つ以上が並行した時間」「最大の並行数」を出す
function sweepByDay(intervals) {
  const byDay = new Map();
  for (const [a, b] of intervals) {
    let s = a;
    while (s < b) {
      const cut = Math.min(b, nextLocalMidnight(s));
      const d = localDate(s);
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push([s, cut]);
      s = cut;
    }
  }
  const out = new Map();
  for (const [d, iv] of byDay) {
    const ev = [];
    let sum = 0;
    for (const [a, b] of iv) { ev.push([a, 1], [b, -1]); sum += b - a; }
    ev.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    let c = 0, prev = null, any = 0, par = 0, max = 0;
    for (const [t, k] of ev) {
      if (prev !== null) {
        if (c >= 1) any += t - prev;
        if (c >= 2) par += t - prev;
      }
      c += k;
      max = Math.max(max, c);
      prev = t;
    }
    out.set(d, { any, par, max, sum });
  }
  return out;
}

function cmdSummary(a) {
  if (!a.in || a.in === true) die("--in <DIR> が要る");
  const files = fs.readdirSync(a.in).filter((n) => n.endsWith("-spans.csv"));
  if (!files.length) die(`${a.in} に *-spans.csv が無い`);
  const since = a.since && a.since !== true ? String(a.since) : "";
  const until = a.until && a.until !== true ? String(a.until) : "";
  const inRange = (d) => (!since || d >= since) && (!until || d <= until);
  const rows = files.flatMap((n) => readCsv(path.join(a.in, n)));
  const iv = (r) => [Date.parse(r.start), Date.parse(r.end)];
  const machines = [...new Set(rows.map((r) => r.machine))].sort();
  const daily = [];
  const minutes = (ms) => ms / 60000;
  const fmtH = (ms) => (ms / 3600000).toFixed(1);
  const lines = [];
  const report = (label, rs) => {
    const all = sweepByDay(rs.map(iv));
    const cats = {};
    for (const c of ["cli", "vscode", "other"]) cats[c] = sweepByDay(rs.filter((r) => r.category === c).map(iv));
    const tok = new Map(), pr = new Map(), ses = new Map();
    for (const r of rs) {
      const d = localDate(Date.parse(r.start));
      tok.set(d, (tok.get(d) || 0) + Number(r.output_tokens || 0));
      pr.set(d, (pr.get(d) || 0) + Number(r.prompts || 0));
      if (!ses.has(d)) ses.set(d, new Set());
      ses.get(d).add(r.machine + "/" + r.session_id);
    }
    let days = 0, any = 0, par = 0, sum = 0, max = 0, out = 0, prompts = 0, sessions = 0;
    const catAny = { cli: 0, vscode: 0, other: 0 };
    for (const d of [...all.keys()].sort()) {
      if (!inRange(d)) continue;
      const x = all.get(d);
      days++; any += x.any; par += x.par; sum += x.sum; max = Math.max(max, x.max);
      out += tok.get(d) || 0; prompts += pr.get(d) || 0; sessions += ses.get(d) ? ses.get(d).size : 0;
      for (const c of Object.keys(catAny)) catAny[c] += cats[c].get(d) ? cats[c].get(d).any : 0;
      daily.push({
        machine: label, date: d,
        active_min: minutes(x.any).toFixed(1),
        session_min_sum: minutes(x.sum).toFixed(1),
        parallel_min: minutes(x.par).toFixed(1),
        max_concurrency: x.max,
        cli_min: minutes(cats.cli.get(d) ? cats.cli.get(d).any : 0).toFixed(1),
        vscode_min: minutes(cats.vscode.get(d) ? cats.vscode.get(d).any : 0).toFixed(1),
        other_min: minutes(cats.other.get(d) ? cats.other.get(d).any : 0).toFixed(1),
        sessions: ses.get(d) ? ses.get(d).size : 0,
        prompts: pr.get(d) || 0,
        output_tokens: tok.get(d) || 0,
      });
    }
    const catSum = catAny.cli + catAny.vscode + catAny.other;
    const pct = (v) => (catSum ? ((100 * v) / catSum).toFixed(0) : "0") + "%";
    lines.push(
      `${label}: 使った日 ${days} 日、使用 ${fmtH(any)} 時間（使った日の平均 ${days ? fmtH(any / days) : "0"} 時間）、` +
        `セッション延べ ${fmtH(sum)} 時間（平均並行 ${any ? (sum / any).toFixed(2) : "0"}）、` +
        `2つ以上並行 ${fmtH(par)} 時間、最大並行 ${max}、` +
        `CLI ${pct(catAny.cli)}・VS Code ${pct(catAny.vscode)}・その他 ${pct(catAny.other)}、` +
        `セッション ${sessions}（日をまたぐものは各日に数える）、依頼 ${prompts}、出力トークン ${out}`
    );
  };
  for (const m of machines) report(m, rows.filter((r) => r.machine === m));
  if (machines.length > 1) report("全端末", rows);
  process.stdout.write(lines.join("\n") + "\n");
  if (a["daily-out"] && a["daily-out"] !== true) {
    writeCsv(String(a["daily-out"]), Object.keys(daily[0] || { machine: "" }), daily);
    process.stdout.write(`${a["daily-out"]}\n`);
  }
}

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0];
if (cmd === "export") cmdExport(args);
else if (cmd === "summary") cmdSummary(args);
else die("使い方: claude-usage.mjs export --machine <端末名> --out <DIR> | summary --in <DIR> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--daily-out <CSV>]");
