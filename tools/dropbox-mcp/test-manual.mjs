// dropbox-mcp-local の手動疎通確認スクリプト。
// Usage: node test-manual.mjs [<dropboxFilePath>]
//   引数省略時は dropbox_list_folder（ルート）と dropbox_auth_status のみ確認する。
//   引数を渡すと、そのファイルを一時ディレクトリへダウンロードしてサイズ・sha256を表示する
//   （list_folder の size と突き合わせてバイト単位で壊れていないか確認できる）。
import * as os from "os";
import * as path from "path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const SERVER = path.join(import.meta.dirname, "index.mjs");
const targetFile = process.argv[2];

const transport = new StdioClientTransport({
  command: "node",
  args: [SERVER],
  env: {
    DROPBOX_APP_KEY: process.env.DROPBOX_APP_KEY,
    DROPBOX_APP_SECRET: process.env.DROPBOX_APP_SECRET,
    HOME: process.env.HOME,
    PATH: process.env.PATH,
  },
});

const client = new Client({ name: "dropbox-mcp-local-test", version: "0.0.1" }, { capabilities: {} });
await client.connect(transport);

const tools = await client.listTools();
console.log("TOOLS:", tools.tools.map((t) => t.name).join(", "));

const authStatus = await client.callTool({ name: "dropbox_auth_status", arguments: {} });
console.log("AUTH STATUS:", authStatus.content[0].text);

const list = await client.callTool({ name: "dropbox_list_folder", arguments: { folderPath: "" } });
console.log("ROOT ENTRIES:", JSON.parse(list.content[0].text).length);

if (targetFile) {
  const destPath = path.join(os.tmpdir(), "dropbox-mcp-local-test", path.basename(targetFile));
  const dl = await client.callTool({ name: "dropbox_download", arguments: { filePath: targetFile, destPath } });
  console.log("DOWNLOAD RESULT:", dl.content[0].text, dl.isError ? "(ERROR)" : "");
}

await client.close();
process.exit(0);
