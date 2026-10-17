// dsh-pet 桌宠 · 局域网只读事件流（手机端联动的最小传输层）
//
// 定位：本文件是桥接目录的**第二个消费者**，只读、可关闭、默认不启动。
// 桌宠（桌面端）用 `pet/agent_link.py` 的 DirGlobTailer 直接 tail 桥目录；手机
// 端拿不到那个目录，因此把同样的 jsonl 用 HTTP 转发出去——**不产生第二种事件
// 协议**：响应里的 records 就是桥目录里逐行的原始记录（`agent-event/v1`
// 信封，见 docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md），字段、语义、
// 脱敏口径全部沿用桥接插件写盘的那一份。
//
// 与 index.js 的关系（为什么是独立文件，不是插件的一部分）：
// 1) 插件有零依赖红线，并且 `verify_import.mjs` 禁止 index.js 出现动态 import、
//    只把 index.js/package.json/cordis.patch.yml 拷进 hermetic 目录做冒烟——
//    静态 import 一个新文件会让那条冒烟失败，动态 import 则被明令禁止。
// 2) 因此本文件**不被 index.js import**：插件树启动路径完全不变（零风险），
//    本进程由用户显式启动（见文件末尾 CLI）。DSH 不启动它，它就完全不存在。
//
// 安全口径（默认最保守，放开需用户显式动作）：
// - 默认绑定 127.0.0.1：手机连不上，必须用户显式 `--host 0.0.0.0`（或局域网
//   IP）。绑定到非回环地址时会额外打印一条醒目提示。
// - 必须持令牌：`Authorization: Bearer <token>`（兼容 `?token=`）。令牌缺失或
//   不匹配一律 401，比较用 sha256+timingSafeEqual（定长，避免按长度泄漏）。
// - 只读：只接受 GET/HEAD；不写事件文件、不改 mtime、不触发桥接插件任何代码。
// - 明文 HTTP：记录本身已脱敏（不落正文/命令/结果摘要），但仍是本机 agent 的
//   活动元数据；PR 描述与 docs 里已把「仅限可信局域网」写成前置条件。
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import os from "node:os";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

/** 响应信封版本。客户端按它拒绝未知版本，服务端变更时递增。 */
export const SCHEMA = "dsh-pet-lan/v1";
export const DEFAULT_HOST = "127.0.0.1";
export const DEFAULT_PORT = 8768;
/** 单次响应最多转发多少字节的事件行；超出的留到下一次轮询（truncated=true）。 */
export const DEFAULT_MAX_BYTES = 256 * 1024;
export const TOKEN_FILE_NAME = "lan-token.txt";
/** 单行事件的硬上限：只是防止「一行没有换行」把游标卡死，不是业务限制。 */
const HARD_LINE_CAP = 8 * 1024 * 1024;
/** 消费端 glob 与桌宠一致：dsh*.jsonl（兼容旧单文件 dsh.jsonl）。 */
const EVENTS_RE = /^dsh.*\.jsonl$/;
const CURSOR_VERSION = 1;

// 桥目录必须与桌宠端/pet 侧一致：win32=%APPDATA%，darwin=~/Library/Application
// Support，其他=~/.config（权威实现在 index.js:71-80，此处刻意重复一份：
// 本文件不 import 插件（见文件头第 2 条），两份规则由
// tests/test_bridge_lan_stream.js 的 bridgeDir 用例钉住）。
export function bridgeDir() {
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || os.homedir(), "dsh-pet-bridge");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "dsh-pet-bridge");
  }
  return path.join(os.homedir(), ".config", "dsh-pet-bridge");
}

/** 读或建令牌文件（0600）。令牌只落盘，不打印——打印需显式 --print-token。 */
export function ensureToken(tokenFile, explicit) {
  if (explicit) return String(explicit);
  try {
    const existing = fs.readFileSync(tokenFile, "utf8").trim();
    if (existing) return existing;
  } catch {
    // 不存在/不可读：下面重建
  }
  const token = randomBytes(16).toString("hex");
  fs.mkdirSync(path.dirname(tokenFile), { recursive: true });
  fs.writeFileSync(tokenFile, `${token}\n`, { encoding: "utf8", mode: 0o600 });
  return token;
}

/** 游标：不透明字符串（base64url 的 {v,f:{文件名:字节偏移}}）。客户端只回传。 */
export function encodeCursor(files) {
  const body = JSON.stringify({ v: CURSOR_VERSION, f: files || {} });
  return Buffer.from(body, "utf8").toString("base64url");
}

/** 解游标；任何非法输入一律返回 null（调用方按「挂载」处理，不回放历史）。 */
export function decodeCursor(text) {
  if (typeof text !== "string" || !text) return null;
  try {
    const parsed = JSON.parse(Buffer.from(text, "base64url").toString("utf8"));
    if (!parsed || parsed.v !== CURSOR_VERSION) return null;
    const raw = parsed.f;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const files = {};
    for (const [name, offset] of Object.entries(raw)) {
      if (!EVENTS_RE.test(name)) continue;
      if (!Number.isFinite(offset) || offset < 0) continue;
      files[name] = Math.floor(offset);
    }
    return files;
  } catch {
    return null;
  }
}

function listEventFiles(dir) {
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch {
    return []; // 目录还不存在（DSH 从未启动）：照常服务，空事件流
  }
  return names.filter((name) => EVENTS_RE.test(name)).sort();
}

function sizeOf(full) {
  try {
    const stat = fs.statSync(full);
    return stat.isFile() ? stat.size : null;
  } catch {
    return null;
  }
}

/**
 * 只读到**最后一个完整行**为止：半个行留到下一次轮询，绝不会把一条被截断的
 * 记录交出去（与桌宠侧 tail 的完整行口径一致）。
 */
function readCompleteLines(full, offset, maxBytes) {
  const size = sizeOf(full);
  if (size === null || size <= offset) return { text: "", consumed: 0 };
  const rest = size - offset;
  let window = Math.min(maxBytes, rest);
  let buffer = readAt(full, offset, window);
  let end = buffer.lastIndexOf(0x0a);
  if (end < 0 && window < rest) {
    // 单行超过一轮预算：把窗口放到整段（硬上限 HARD_LINE_CAP）再找一次换行，
    // 否则游标会一直停在同一个字节、永远读不出记录。
    window = Math.min(HARD_LINE_CAP, rest);
    buffer = readAt(full, offset, window);
    end = buffer.lastIndexOf(0x0a);
  }
  if (end < 0) return { text: "", consumed: 0 };
  return { text: buffer.subarray(0, end + 1).toString("utf8"), consumed: end + 1 };
}

function readAt(full, offset, length) {
  const buffer = Buffer.allocUnsafe(length);
  const fd = fs.openSync(full, "r");
  try {
    fs.readSync(fd, buffer, 0, length, offset);
  } finally {
    fs.closeSync(fd);
  }
  return buffer;
}

/** 挂载游标：每个事件文件都停在**当前 EOF**（桌宠侧同样是「不回放历史」）。 */
export function snapshotCursor(dir) {
  const files = {};
  for (const name of listEventFiles(dir)) {
    const size = sizeOf(path.join(dir, name));
    files[name] = size === null ? 0 : size;
  }
  return files;
}

/**
 * 从游标读增量。`sinceFiles` 里没有的文件从 0 开始读——那是挂载之后新出现的
 * 实例（新 DSH 进程）或刚轮转过的文件，正是客户端要的新信息。
 * 返回的 `files` 是**下一条请求该带的游标**（只有真正交出去的行才推进）。
 */
export function collectSince(dir, sinceFiles, maxBytes = DEFAULT_MAX_BYTES) {
  const records = [];
  const files = {};
  let skipped = 0;
  let truncated = false;
  let budget = Math.max(1, maxBytes);

  for (const name of listEventFiles(dir)) {
    const full = path.join(dir, name);
    const size = sizeOf(full);
    if (size === null) continue; // 读的瞬间被删/不是普通文件：下一轮再说
    const known = Object.prototype.hasOwnProperty.call(sinceFiles, name) ? sinceFiles[name] : null;
    // 未知或文件变小（轮转/重建）都从 0 读；已知且合法则续读。
    const offset = known !== null && known <= size ? known : 0;
    if (budget <= 0) {
      files[name] = offset; // 没轮到的文件保持原游标，下次继续
      truncated = true;
      continue;
    }
    let slice;
    try {
      slice = readCompleteLines(full, offset, budget);
    } catch {
      files[name] = offset;
      continue;
    }
    for (const line of slice.text.split("\n")) {
      if (!line) continue;
      let record;
      try {
        record = JSON.parse(line);
      } catch {
        skipped++; // 坏行照样消费掉，不让它卡住游标
        continue;
      }
      if (record && typeof record === "object" && !Array.isArray(record)) records.push(record);
      else skipped++;
    }
    files[name] = offset + slice.consumed;
    if (files[name] < size) truncated = true; // 这个文件本轮没读完：还有增量
    budget -= slice.consumed;
  }

  return { records, files, skipped, truncated };
}

function digest(value) {
  return createHash("sha256").update(String(value), "utf8").digest();
}

/** 定长比较：不因令牌长度不同而泄漏信息。 */
function tokenOk(expected, provided) {
  if (!provided) return false;
  const a = digest(expected);
  const b = digest(provided);
  return timingSafeEqual(a, b) || false;
}

function bearerOf(req, url) {
  const header = String(req.headers.authorization || "");
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  if (match) return match[1].trim();
  const query = url.searchParams.get("token");
  return query ? String(query).trim() : "";
}

function sendJson(res, status, payload, extraHeaders = {}) {
  const body = `${JSON.stringify(payload)}\n`;
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
    ...extraHeaders,
  });
  res.end(body);
}

/**
 * 创建只读服务器（不 listen）。测试直接拿它请求，生产走 startLanStream。
 * `dir` 缺省取 bridgeDir()，`token` 缺省取 bridge-dir/lan-token.txt。
 */
export function createLanStreamServer(options = {}) {
  const dir = options.dir || bridgeDir();
  const tokenFile = options.tokenFile || path.join(dir, TOKEN_FILE_NAME);
  const token = ensureToken(tokenFile, options.token);
  const maxBytes = Number.isFinite(options.maxBytes) && options.maxBytes > 0
    ? options.maxBytes
    : DEFAULT_MAX_BYTES;

  const server = http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url || "/", "http://127.0.0.1");
    } catch {
      sendJson(res, 400, { error: "bad_request" });
      return;
    }
    if (req.method !== "GET" && req.method !== "HEAD") {
      sendJson(res, 405, { error: "method_not_allowed" }, { allow: "GET, HEAD" });
      return;
    }
    if (url.pathname !== "/v1/events") {
      sendJson(res, 404, { error: "not_found" });
      return;
    }
    if (!tokenOk(token, bearerOf(req, url))) {
      sendJson(res, 401, { error: "unauthorized" });
      return;
    }

    const since = decodeCursor(url.searchParams.get("since"));
    let payload;
    try {
      if (since === null) {
        // 无游标/坏游标 = 挂载：只给一个 EOF 游标，不回放历史。
        payload = { schema: SCHEMA, cursor: encodeCursor(snapshotCursor(dir)), records: [],
          skipped: 0, truncated: false };
      } else {
        const collected = collectSince(dir, since, maxBytes);
        payload = { schema: SCHEMA, cursor: encodeCursor(collected.files),
          records: collected.records, skipped: collected.skipped,
          truncated: collected.truncated };
      }
    } catch {
      sendJson(res, 500, { error: "internal" });
      return;
    }
    if (req.method === "HEAD") {
      res.writeHead(200, { "cache-control": "no-store" });
      res.end();
      return;
    }
    sendJson(res, 200, payload);
  });
  return { server, dir, token, tokenFile, maxBytes };
}

/** 起监听并把真实地址交回去（`port: 0` 时由系统分配）。 */
export function startLanStream(options = {}) {
  const built = createLanStreamServer(options);
  const host = options.host || DEFAULT_HOST;
  const port = Number.isFinite(options.port) ? options.port : DEFAULT_PORT;
  return new Promise((resolve, reject) => {
    const fail = (err) => reject(err);
    built.server.once("error", fail);
    built.server.listen(port, host, () => {
      built.server.removeListener("error", fail);
      const address = built.server.address();
      const bound = address && typeof address === "object" ? address.port : port;
      resolve({
        server: built.server,
        host,
        port: bound,
        url: `http://${host}:${bound}/v1/events`,
        token: built.token,
        tokenFile: built.tokenFile,
        dir: built.dir,
        close: () => new Promise((done) => built.server.close(() => done())),
      });
    });
  });
}

// ===== CLI =====
// 用户显式启动；DSH 与插件树都不需要它，停掉即完全消失。
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const take = () => (i + 1 < argv.length ? argv[i + 1] : undefined);
    if (key === "--host") { out.host = take(); i += 1; } else if (key === "--port") { out.port = Number(take()); i += 1; } else if (key === "--token") { out.token = take(); i += 1; } else if (key === "--token-file") { out.tokenFile = take(); i += 1; } else if (key === "--bridge-dir") { out.dir = take(); i += 1; } else if (key === "--print-token") { out.printToken = true; } else if (key === "--help" || key === "-h") { out.help = true; }
  }
  return out;
}

function loopbackOnly(host) {
  return host === "127.0.0.1" || host === "::1" || host === "localhost";
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log([
      "用法: node integrations/dsh-pet-bridge/lan-stream.js [选项]",
      "",
      "  --bridge-dir <目录>   桥目录（默认与桌宠一致：%APPDATA%/dsh-pet-bridge 等）",
      "  --host <地址>         监听地址，默认 127.0.0.1；手机访问需 --host 0.0.0.0",
      "  --port <端口>         监听端口，默认 8768",
      "  --token <令牌>        直接指定令牌（默认读写 <桥目录>/lan-token.txt）",
      "  --token-file <路径>   令牌文件路径",
      "  --print-token         启动时打印令牌（配对用；默认只打印文件路径）",
      "",
      "只读端点: GET /v1/events[?since=<cursor>]  需要 Authorization: Bearer <令牌>",
    ].join("\n"));
    return;
  }
  const started = await startLanStream(args);
  console.log(`[dsh-pet lan] listening on http://${started.host}:${started.port}/v1/events`);
  console.log(`[dsh-pet lan] bridge dir: ${started.dir}`);
  console.log(`[dsh-pet lan] token file: ${started.tokenFile}`);
  if (args.printToken) console.log(`[dsh-pet lan] token: ${started.token}`);
  if (loopbackOnly(started.host)) {
    console.log("[dsh-pet lan] 仅回环地址：手机无法连接。要让手机连，显式传 --host 0.0.0.0"
      + "（事件已脱敏，但仍仅建议在可信局域网内使用）。");
  } else {
    console.log("[dsh-pet lan] 已绑定非回环地址：局域网内持有令牌的设备可读取本机 agent 活动元数据。"
      + "用完请停掉本进程。");
  }
  const stop = () => { started.close().then(() => process.exit(0)); };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

// 只有被直接执行时才起 CLI；被测试/其他模块 import 时保持纯函数面。
const invokedDirectly = (() => {
  try {
    return process.argv[1] && path.resolve(process.argv[1]) === path.resolve(decodeURIComponent(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")));
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  main().catch((err) => {
    console.error(`[dsh-pet lan] 启动失败: ${err && err.message ? err.message : err}`);
    process.exit(1);
  });
}
