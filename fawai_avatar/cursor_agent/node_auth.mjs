#!/usr/bin/env node
/**
 * Cursor Agent 浏览器登录（调用 cursor-sdk 自带的 @cursor/sdk）。
 * 用法：node node_auth.mjs <status|login|logout>
 * 环境：FAWAI_CURSOR_SDK_NODE = @cursor/sdk 包根目录
 * login 时在 stderr 打 JSON 行 {type:"login_url",url}；stdout 打最终 JSON。
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const sdkRoot = String(process.env.FAWAI_CURSOR_SDK_NODE || "").trim();
if (!sdkRoot) {
  process.stderr.write(JSON.stringify({ ok: false, error: "FAWAI_CURSOR_SDK_NODE missing" }) + "\n");
  process.exit(2);
}

const require = createRequire(import.meta.url);

async function loadCursor() {
  // 优先 ESM 入口；失败再走 createRequire（兼容 CJS / 目录主入口）
  try {
    const mod = await import(pathToFileURL(`${sdkRoot}/dist/esm/index.js`).href);
    if (mod && mod.Cursor) return mod.Cursor;
    if (mod && mod.default && mod.default.Cursor) return mod.default.Cursor;
  } catch {
    /* fall through */
  }
  const mod = require(sdkRoot);
  return mod.Cursor || (mod.default && mod.default.Cursor);
}

const cmd = String(process.argv[2] || "status").trim();

function writeOut(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function writeErrLine(obj) {
  process.stderr.write(JSON.stringify(obj) + "\n");
}

async function main() {
  const Cursor = await loadCursor();
  if (!Cursor || !Cursor.auth) {
    writeOut({ ok: false, error: "无法从 @cursor/sdk 加载 Cursor.auth" });
    process.exit(1);
  }
  if (cmd === "status") {
    const s = await Cursor.auth.status();
    writeOut({ ok: true, ...s });
    return;
  }
  if (cmd === "logout") {
    await Cursor.auth.logout();
    writeOut({ ok: true, status: "logged-out" });
    return;
  }
  if (cmd === "login") {
    const openBrowser = process.env.FAWAI_CURSOR_OPEN_BROWSER !== "0";
    const result = await Cursor.auth.login({
      openBrowser,
      onLoginUrl: (url) => {
        writeErrLine({ type: "login_url", url: String(url || "") });
      },
      apiKeyName: process.env.FAWAI_CURSOR_API_KEY_NAME || "fawai-avatar",
    });
    writeOut({
      ok: true,
      status: "logged-in",
      email: result && result.email ? result.email : "",
      apiKeyExpiresAtMs: result && result.apiKeyExpiresAtMs != null ? result.apiKeyExpiresAtMs : null,
      // 不回传 apiKey 给调用方 stdout（已写入 ~/.cursor/sdk/auth.json）
    });
    return;
  }
  writeOut({ ok: false, error: `unknown command: ${cmd}` });
  process.exit(2);
}

main().catch((err) => {
  writeOut({
    ok: false,
    error: String((err && err.message) || err || "auth failed"),
  });
  process.exit(1);
});
