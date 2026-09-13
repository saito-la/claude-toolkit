#!/usr/bin/env node
// dropbox-mcp-local — 自前実装の Dropbox MCP サーバー
//
// @fm-phibia/dropbox-mcp の置き換え。あのパッケージは HTTPS レスポンスを種類を
// 問わず Buffer.concat(chunks).toString()（既定UTF-8）でデコードしており、
// バイナリファイルの download 応答本体もここを通るため非UTF-8バイト列が不可逆に
// 壊れていた（詳細: docs/mcp/20260907-dropbox-mcp-binary-corruption.md）。
//
// 設計方針（本実装での修正点）:
// - ダウンロードはレスポンスストリームを直接ファイルへ書き出す（文字列化しない）。
//   会話コンテキストには保存先パス・サイズ・sha256 だけを返す。バイナリを
//   base64 化して tool result に載せる設計もトークンを浪費するため採らない。
// - アップロードはローカルファイルパスを受け取りバイト列のまま POST する
//   （content を文字列で受け取ると同じ壊れ方をするため、その入力経路自体を廃止）。
// - JSON を返すエンドポイント（トークン交換・list_folder 等）だけを文字列/JSON
//   としてデコードする。バイナリ経路とは完全に関数を分ける。
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import * as https from "https";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as crypto from "crypto";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    console.error("Error: DROPBOX_APP_KEY and DROPBOX_APP_SECRET environment variables are required.");
    process.exit(1);
  }
  return value;
}

const APP_KEY = requireEnv("DROPBOX_APP_KEY");
const APP_SECRET = requireEnv("DROPBOX_APP_SECRET");
const TOKEN_FILE_PATH = process.env.DROPBOX_TOKEN_FILE ?? path.join(os.homedir(), ".dropbox_token");
const DEFAULT_DOWNLOAD_DIR = path.join(os.homedir(), "Downloads", "dropbox-mcp");

function getAuthUrl() {
  return `https://www.dropbox.com/oauth2/authorize?client_id=${APP_KEY}&response_type=code&token_access_type=offline`;
}

async function openBrowser(url) {
  const platform = process.platform;
  try {
    if (platform === "darwin") {
      await execFileAsync("open", [url]);
      return;
    }
    if (platform === "win32") {
      throw new Error("Automatic browser open is not supported on Windows; open manually.");
    }
    await execFileAsync("xdg-open", [url]);
  } catch {
    console.error(`Please open this URL manually: ${url}`);
  }
}

function saveRefreshToken(token) {
  fs.writeFileSync(TOKEN_FILE_PATH, token, { encoding: "utf-8", mode: 0o600 });
}

function loadRefreshToken() {
  if (fs.existsSync(TOKEN_FILE_PATH)) {
    return fs.readFileSync(TOKEN_FILE_PATH, "utf-8").trim();
  }
  return null;
}

async function getRefreshToken() {
  let refreshToken = process.env.DROPBOX_REFRESH_TOKEN;
  if (!refreshToken) {
    refreshToken = loadRefreshToken() ?? undefined;
  }
  if (!refreshToken) {
    throw new Error([
      "Dropbox refresh token is not configured.",
      "Set DROPBOX_REFRESH_TOKEN (recommended) or create a token file.",
      "You can also use MCP tools:",
      "- dropbox_auth_get_url (open the URL, authorize, copy the code)",
      "- dropbox_auth_exchange_code (exchange code -> refresh token; optionally save)",
    ].join("\n"));
  }
  return refreshToken;
}

// JSON を返すエンドポイント専用（トークン交換・list_folder・upload の結果 JSON）。
// バイナリを返しうるエンドポイントには絶対に使わない。
function requestJson(hostname, reqPath, method, headers, body) {
  return new Promise((resolve, reject) => {
    const req = https.request({ hostname, path: reqPath, method, headers }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const data = Buffer.concat(chunks).toString("utf-8");
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve(data);
          }
        } else {
          reject(new Error(`Request failed: ${res.statusCode} - ${data}`));
        }
      });
    });
    req.setTimeout(30_000, () => req.destroy(new Error("Request timed out")));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function getAccessToken() {
  const refreshToken = await getRefreshToken();
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    client_id: APP_KEY,
    client_secret: APP_SECRET,
  }).toString();
  const response = await requestJson("api.dropboxapi.com", "/oauth2/token", "POST", {
    "Content-Type": "application/x-www-form-urlencoded",
    "Content-Length": Buffer.byteLength(body).toString(),
  }, body);
  return response.access_token;
}

async function exchangeCodeForToken(authCode) {
  const body = new URLSearchParams({
    code: authCode,
    grant_type: "authorization_code",
    client_id: APP_KEY,
    client_secret: APP_SECRET,
  }).toString();
  return requestJson("api.dropboxapi.com", "/oauth2/token", "POST", {
    "Content-Type": "application/x-www-form-urlencoded",
    "Content-Length": Buffer.byteLength(body).toString(),
  }, body);
}

// Dropbox-API-Arg ヘッダは ASCII しか通らないため非ASCIIをエスケープする。
function escapeNonAscii(str) {
  return Array.from(str)
    .map((ch) => {
      const code = ch.codePointAt(0);
      return code > 0x7e ? "\\u" + code.toString(16).padStart(4, "0") : ch;
    })
    .join("");
}

function uniqueDestPath(destPath) {
  if (!fs.existsSync(destPath)) return destPath;
  const ext = path.extname(destPath);
  const base = destPath.slice(0, destPath.length - ext.length);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `${base}-${stamp}${ext}`;
}

// バイナリ本体を文字列化せず、レスポンスストリームを直接ファイルへ書き出す。
async function downloadToFile(dropboxPath, destPath) {
  const accessToken = await getAccessToken();
  fs.mkdirSync(path.dirname(destPath), { recursive: true });
  const finalPath = uniqueDestPath(destPath);

  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: "content.dropboxapi.com",
      path: "/2/files/download",
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Dropbox-API-Arg": escapeNonAscii(JSON.stringify({ path: dropboxPath })),
      },
    }, (res) => {
      if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
        const hash = crypto.createHash("sha256");
        let bytes = 0;
        const out = fs.createWriteStream(finalPath);
        res.on("data", (chunk) => {
          hash.update(chunk);
          bytes += chunk.length;
        });
        res.pipe(out);
        out.on("finish", () => resolve({ savedTo: finalPath, bytes, sha256: hash.digest("hex") }));
        out.on("error", reject);
        res.on("error", reject);
      } else {
        // エラー応答は小さな JSON なのでバッファして読んでよい。
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          reject(new Error(`Download failed: ${res.statusCode} - ${Buffer.concat(chunks).toString("utf-8")}`));
        });
      }
    });
    req.setTimeout(120_000, () => req.destroy(new Error("Request timed out")));
    req.on("error", reject);
    req.end();
  });
}

// アップロードもローカルファイルをバイト列のまま読んで POST する（文字列を経由しない）。
async function uploadFromFile(dropboxPath, srcPath) {
  const accessToken = await getAccessToken();
  const content = fs.readFileSync(srcPath);
  return requestJson("content.dropboxapi.com", "/2/files/upload", "POST", {
    Authorization: `Bearer ${accessToken}`,
    "Dropbox-API-Arg": escapeNonAscii(JSON.stringify({
      path: dropboxPath,
      mode: "add",
      autorename: true,
      mute: false,
    })),
    "Content-Type": "application/octet-stream",
    "Content-Length": content.length.toString(),
  }, content);
}

async function listFolder(folderPath) {
  const accessToken = await getAccessToken();
  const body = JSON.stringify({
    path: folderPath === "/" ? "" : folderPath,
    recursive: false,
    include_media_info: false,
    include_deleted: false,
    include_has_explicit_shared_members: false,
  });
  const result = await requestJson("api.dropboxapi.com", "/2/files/list_folder", "POST", {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }, body);
  return result.entries;
}

const server = new Server(
  { name: "dropbox-mcp-local", version: "1.0.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "dropbox_auth_status",
      description: "Check whether Dropbox refresh token is configured (env or token file)",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "dropbox_auth_get_url",
      description: "Get Dropbox OAuth authorization URL (optionally opens browser on the MCP host)",
      inputSchema: {
        type: "object",
        properties: {
          openBrowser: { type: "boolean", description: "If true, attempt to open the authorization URL in a browser on the MCP host" },
        },
      },
    },
    {
      name: "dropbox_auth_exchange_code",
      description: "Exchange an authorization code for tokens. Optionally save refresh token to token file.",
      inputSchema: {
        type: "object",
        properties: {
          authCode: { type: "string", description: "Authorization code from Dropbox OAuth redirect" },
          save: { type: "boolean", description: "If true (default), save refresh token to token file for future use" },
        },
        required: ["authCode"],
      },
    },
    {
      name: "dropbox_list_folder",
      description: "List files and folders in a Dropbox directory",
      inputSchema: {
        type: "object",
        properties: {
          folderPath: { type: "string", description: "The path to the folder in Dropbox (e.g., /path/to/folder; empty for root)" },
        },
        required: ["folderPath"],
      },
    },
    {
      name: "dropbox_download",
      description: "Download a file from Dropbox directly to local disk (never inlines file bytes into the tool result — returns the saved path, size, and sha256 only). Safe for binary files.",
      inputSchema: {
        type: "object",
        properties: {
          filePath: { type: "string", description: "The path to the file in Dropbox (e.g., /path/to/file.xlsx)" },
          destPath: { type: "string", description: "Local destination file path (optional; default: ~/Downloads/dropbox-mcp/<basename>). Auto-renamed with a timestamp if it already exists." },
        },
        required: ["filePath"],
      },
    },
    {
      name: "dropbox_upload",
      description: "Upload a local file to Dropbox by path (reads the local file as raw bytes; does not accept inline file content as a string, to avoid the same binary-corruption failure mode on the write path)",
      inputSchema: {
        type: "object",
        properties: {
          filePath: { type: "string", description: "The destination path in Dropbox (e.g., /path/to/file.txt)" },
          srcPath: { type: "string", description: "Local source file path to upload" },
        },
        required: ["filePath", "srcPath"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    switch (name) {
      case "dropbox_auth_status": {
        const hasEnv = Boolean(process.env.DROPBOX_REFRESH_TOKEN);
        const hasFile = fs.existsSync(TOKEN_FILE_PATH);
        return { content: [{ type: "text", text: JSON.stringify({ configured: hasEnv || hasFile, source: hasEnv ? "env" : hasFile ? "file" : null, tokenFile: TOKEN_FILE_PATH }, null, 2) }] };
      }
      case "dropbox_auth_get_url": {
        const url = getAuthUrl();
        if (args?.openBrowser) await openBrowser(url);
        return { content: [{ type: "text", text: url }] };
      }
      case "dropbox_auth_exchange_code": {
        const { authCode, save } = args;
        const response = await exchangeCodeForToken(authCode);
        const refreshToken = response.refresh_token;
        const shouldSave = save !== false;
        if (shouldSave && refreshToken) saveRefreshToken(refreshToken);
        return { content: [{ type: "text", text: JSON.stringify({ refresh_token: refreshToken, saved: shouldSave, tokenFile: shouldSave ? TOKEN_FILE_PATH : undefined }, null, 2) }] };
      }
      case "dropbox_list_folder": {
        const { folderPath } = args;
        const entries = await listFolder(folderPath);
        return { content: [{ type: "text", text: JSON.stringify(entries, null, 2) }] };
      }
      case "dropbox_download": {
        const { filePath, destPath } = args;
        const dest = destPath || path.join(DEFAULT_DOWNLOAD_DIR, path.basename(filePath));
        const result = await downloadToFile(filePath, dest);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      }
      case "dropbox_upload": {
        const { filePath, srcPath } = args;
        const result = await uploadFromFile(filePath, srcPath);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      }
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  } catch (error) {
    return { content: [{ type: "text", text: `Error: ${error.message}` }], isError: true };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
