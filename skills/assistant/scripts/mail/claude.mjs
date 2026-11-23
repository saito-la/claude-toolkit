/**
 * Claude Code を非対話で1回呼び、JSON Schema で縛った答えを返す（メールの自動処理の判断に使う）。
 *
 * 定額のプランの中で動かすので API キーは使わない。--setting-sources project で使い手のフックと許可を読まず、
 * --strict-mcp-config で MCP をつながず、道具も渡さない。無人で確認待ちにならないよう --permission-mode dontAsk にする。
 * 会話は保存しない。
 *
 * Windows では npm で入れた claude は claude.cmd で、shell を介さずには起動できない。shell を介すと引数の
 * JSON Schema の引用が崩れるので、claude.exe（ネイティブの導入）を探して直に起動し、無ければ名前を出して止まる。
 */
import { spawn, execFileSync } from 'child_process';

let resolved = null;
function claudeCommand() {
  if (resolved) return resolved;
  if (process.platform !== 'win32') return (resolved = 'claude');
  let found = [];
  try {
    found = execFileSync('where', ['claude'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  } catch { /* 見つからない */ }
  const exe = found.find((p) => /\.exe$/i.test(p));
  if (!exe) throw new Error(`claude.exe が見つからない（where claude の結果：${found.join(', ') || 'なし'}）。ネイティブの導入（claude install）を使う`);
  return (resolved = exe);
}

/** { prompt, model, effort, schema, cwd, timeoutMin, label } → structured_output */
export function runClaude({ prompt, model, effort, schema, cwd, timeoutMin = 10, label = 'claude' }) {
  const args = ['-p', '--model', model, '--output-format', 'json', '--strict-mcp-config',
    '--setting-sources', 'project', '--permission-mode', 'dontAsk', '--no-session-persistence'];
  if (effort) args.push('--effort', effort);
  if (schema) args.push('--json-schema', JSON.stringify(schema));
  const t0 = Date.now();
  return new Promise((resolve, reject) => {
    let ch;
    try {
      ch = spawn(claudeCommand(), args, { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (e) { return reject(e); }
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => ch.kill(), timeoutMin * 60_000);
    ch.stdout.on('data', (d) => { stdout += d; });
    ch.stderr.on('data', (d) => { stderr += d; });
    ch.on('error', (e) => { clearTimeout(timer); reject(new Error(`${label}: claude を起動できない（${e.code || e.message}）`)); });
    ch.on('close', (code, signal) => {
      clearTimeout(timer);
      let out;
      try { out = JSON.parse(stdout); } catch {
        return reject(new Error(`${label}: claude の出力を JSON として読めない（exit ${code}${signal ? ' ' + signal : ''}）: ${(stdout || stderr).slice(0, 300)}`));
      }
      if (out.is_error) return reject(new Error(`${label}: claude がエラーを返した: ${String(out.result || out.subtype).slice(0, 300)}`));
      if (schema && !out.structured_output) return reject(new Error(`${label}: structured_output が無い: ${String(out.result).slice(0, 300)}`));
      resolve({ value: schema ? out.structured_output : out.result, seconds: Math.round((Date.now() - t0) / 1000), usage: out.usage || null, cost: out.total_cost_usd ?? null });
    });
    ch.stdin.end(prompt);
  });
}
