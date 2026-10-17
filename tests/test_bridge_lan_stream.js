// 局域网只读事件流（integrations/dsh-pet-bridge/lan-stream.js）契约：
// 手机端消费的必须是**桥接插件写盘的同一份记录**，不能是第二套协议。
//
// 为什么要有这些断言：手机端只拿到 HTTP，拿不到桥目录。若这个端点在转发时
// 换了字段名、补了默认值、或按自己的理解重排事件，桌宠与手机就会从同一台机器
// 上看到两种「事实」。所以这里逐条钉住：
//   1) records 与桥接插件 encodeRecord 的产物逐字段相等（含 inst 归属）；
//   2) 挂载（不带 since）不回放历史，与桌宠侧 tail 的口径一致；
//   3) 只交出完整行：半个行留到下一次轮询；
//   4) 令牌缺失/错误一律 401，只接受 GET/HEAD；
//   5) 读事件流不改事件文件（只读端点，mtime/大小不变）。
//
// 不启动 DSH 宿主：端点只读桥目录里的普通文件，因此这里直接写夹具文件。
import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

let lan;
let bridge;
test.before(async () => {
  lan = await import(pathToFileURL(path.join(bridgeDir, "lan-stream.js")).href);
  bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")).href);
  assert.ok(bridge.__instanceTest, "桥接插件必须导出 __instanceTest");
});

/** 一个临时桥目录 + 一个已监听的端点（端口 0 由系统分配）。 */
async function boot(options = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "dsh-pet-lan-"));
  const started = await lan.startLanStream({ dir, token: "test-token", port: 0, ...options });
  return { dir, started, done: async () => { await started.close(); rmSync(dir, { recursive: true, force: true }); } };
}

function url(started, since) {
  const base = `http://127.0.0.1:${started.port}/v1/events`;
  return since === undefined ? base : `${base}?since=${encodeURIComponent(since)}`;
}

async function get(started, { since, token = "test-token", path: custom, method = "GET", header = true } = {}) {
  const target = custom ? `http://127.0.0.1:${started.port}${custom}` : url(started, since);
  const headers = header && token !== null ? { authorization: `Bearer ${token}` } : {};
  const response = await fetch(target, { method, headers });
  const text = await response.text();
  return { status: response.status, payload: text ? JSON.parse(text) : null };
}

function fixture(dir, name, lines) {
  const full = path.join(dir, name);
  writeFileSync(full, lines.join(""), "utf8");
  return full;
}

test("默认绑定回环：手机连不上必须由用户显式放开", async () => {
  const box = await boot();
  try {
    assert.equal(box.started.host, "127.0.0.1");
  } finally {
    await box.done();
  }
});

test("桥目录解析与桌宠侧一致（win32/APPDATA、darwin、其他）", () => {
  const expected = process.platform === "win32"
    ? path.join(process.env.APPDATA || "", "dsh-pet-bridge")
    : process.platform === "darwin"
      ? path.join("Library", "Application Support", "dsh-pet-bridge")
      : path.join(".config", "dsh-pet-bridge");
  const actual = lan.bridgeDir();
  assert.ok(actual.endsWith(expected), `桥目录规则漂移：${actual} 不以 ${expected} 结尾`);
});

test("令牌缺失/错误一律 401，正确令牌放行", async () => {
  const box = await boot();
  try {
    fixture(box.dir, "dsh-1.jsonl", [bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s1" })]);
    assert.equal((await get(box.started, { token: null })).status, 401, "无令牌必须 401");
    assert.equal((await get(box.started, { token: "wrong" })).status, 401, "错误令牌必须 401");
    assert.equal((await get(box.started, {})).status, 200, "正确令牌必须放行");
    const viaQuery = await fetch(`${url(box.started)}?token=test-token`);
    assert.equal(viaQuery.status, 200, "?token= 是给不能设头的客户端的兼容入口");
  } finally {
    await box.done();
  }
});

test("只读端点：只接受 GET/HEAD，未知路径 404", async () => {
  const box = await boot();
  try {
    assert.equal((await get(box.started, { method: "POST" })).status, 405);
    assert.equal((await get(box.started, { method: "DELETE" })).status, 405);
    assert.equal((await get(box.started, { path: "/v1/other" })).status, 404);
    const head = await fetch(url(box.started), { method: "HEAD", headers: { authorization: "Bearer test-token" } });
    assert.equal(head.status, 200);
  } finally {
    await box.done();
  }
});

test("挂载不回放历史：不带 since 只给游标，之后只交出新增记录", async () => {
  const box = await boot();
  try {
    const first = bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s1", step: "1" });
    fixture(box.dir, "dsh-4242.jsonl", [first]);

    const attach = await get(box.started);
    assert.equal(attach.status, 200);
    assert.deepEqual(attach.payload.records, [], "挂载不得回放历史（与桌宠侧 tail 同口径）");
    assert.ok(attach.payload.cursor, "必须返回游标");
    assert.equal(attach.payload.schema, lan.SCHEMA);

    const second = bridge.__instanceTest.encodeRecord({ event: "turn/end", sessionId: "s1", step: "1", reasonKind: "completed" });
    appendFileSync(path.join(box.dir, "dsh-4242.jsonl"), second, "utf8");

    const poll = await get(box.started, { since: attach.payload.cursor });
    assert.equal(poll.payload.records.length, 1);
    assert.deepEqual(poll.payload.records[0], JSON.parse(second),
      "转发出去的就是桥接插件的原始记录，逐字段相等（不得改名/补字段）");
    assert.equal(poll.payload.records[0].inst, bridge.__instanceTest.bootId, "写者归属必须原样带过去");
    assert.equal(poll.payload.truncated, false);

    const again = await get(box.started, { since: poll.payload.cursor });
    assert.deepEqual(again.payload.records, [], "游标必须推进，同一批不能重复交两次");
  } finally {
    await box.done();
  }
});

test("半行不交出：没有换行的尾部留到下一次轮询", async () => {
  const box = await boot();
  try {
    const attach = await get(box.started);
    const full = bridge.__instanceTest.encodeRecord({ event: "tool/call", sessionId: "s2", tool: "pwsh" });
    const half = Math.floor(full.length / 2);
    const target = path.join(box.dir, "dsh-77.jsonl");
    appendFileSync(target, full.slice(0, half), "utf8");

    const partial = await get(box.started, { since: attach.payload.cursor });
    assert.deepEqual(partial.payload.records, [], "半个行不是记录，绝不能交出");

    appendFileSync(target, full.slice(half), "utf8");
    const complete = await get(box.started, { since: partial.payload.cursor });
    assert.equal(complete.payload.records.length, 1);
    assert.deepEqual(complete.payload.records[0], JSON.parse(full), "补齐换行后只交一次且内容完整");
  } finally {
    await box.done();
  }
});

test("挂载之后新出现的实例文件从 0 读（新 DSH 进程的事件不能被吞掉）", async () => {
  const box = await boot();
  try {
    const attach = await get(box.started);
    const fresh = bridge.__instanceTest.encodeRecord({ event: "approval/request", sessionId: "s3", rpcId: "r1" });
    fixture(box.dir, "dsh-9001.jsonl", [fresh]);

    const poll = await get(box.started, { since: attach.payload.cursor });
    assert.equal(poll.payload.records.length, 1);
    assert.deepEqual(poll.payload.records[0], JSON.parse(fresh));
  } finally {
    await box.done();
  }
});

test("轮转/重写（文件变小）后游标不越界，从 0 重读", async () => {
  const box = await boot();
  try {
    const attach = await get(box.started); // 先挂载：此后出现的文件按「新实例」从 0 读
    const big = bridge.__instanceTest.encodeRecord({ event: "tool/call", sessionId: "s4", tool: "read" });
    const target = fixture(box.dir, "dsh-5.jsonl", [big, big]);
    const consumed = (await get(box.started, { since: attach.payload.cursor })).payload;
    assert.equal(consumed.records.length, 2);

    // 插件在 >1MB 时轮转：文件被重写成一个更短的新文件。
    const fresh = bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s5" });
    writeFileSync(target, fresh, "utf8");

    const poll = await get(box.started, { since: consumed.cursor });
    assert.equal(poll.payload.skipped, 0);
    assert.deepEqual(poll.payload.records.map((r) => r.sessionId), ["s5"], "重写后的内容必须能读到");
  } finally {
    await box.done();
  }
});

test("坏游标按挂载处理：不回放、不报错", async () => {
  const box = await boot();
  try {
    fixture(box.dir, "dsh-6.jsonl", [bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s6" })]);
    for (const bad of ["", "not-a-cursor", "eyJ2Ijo5OSwiZiI6e319"]) {
      const response = await get(box.started, { since: bad });
      assert.equal(response.status, 200, `坏游标必须无害：${bad}`);
      assert.deepEqual(response.payload.records, [], "坏游标不得退化成回放历史");
    }
  } finally {
    await box.done();
  }
});

test("坏行跳过但不卡游标（不让一条脏数据堵死后面的事件）", async () => {
  const box = await boot();
  try {
    const attach = await get(box.started);
    const good = bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s7" });
    appendFileSync(path.join(box.dir, "dsh-8.jsonl"), `{not json}\n${good}`, "utf8");
    const poll = await get(box.started, { since: attach.payload.cursor });
    assert.equal(poll.payload.skipped, 1);
    assert.equal(poll.payload.records.length, 1);
    assert.deepEqual(poll.payload.records[0], JSON.parse(good));
  } finally {
    await box.done();
  }
});

test("单轮预算：超出 cap 的部分留给下一次轮询，不丢记录", async () => {
  const box = await boot({ maxBytes: 220 });
  try {
    const attach = await get(box.started);
    const lines = Array.from({ length: 6 }, (_, i) =>
      bridge.__instanceTest.encodeRecord({ event: "tool/call", sessionId: `cap-${i}`, tool: "grep" }));
    fixture(box.dir, "dsh-9.jsonl", lines);

    const seen = [];
    let cursor = attach.payload.cursor;
    for (let round = 0; round < 40; round += 1) {
      const poll = await get(box.started, { since: cursor });
      seen.push(...poll.payload.records.map((r) => r.sessionId));
      cursor = poll.payload.cursor;
      if (!poll.payload.truncated) break;
    }
    assert.deepEqual(seen, lines.map((line) => JSON.parse(line).sessionId), "多轮轮询必须原序、不重、不丢");
  } finally {
    await box.done();
  }
});

test("读事件流不写事件文件：mtime 与大小都不变", async () => {
  const box = await boot();
  try {
    const target = fixture(box.dir, "dsh-11.jsonl", [bridge.__instanceTest.encodeRecord({ event: "turn/start", sessionId: "s8" })]);
    const before = statSync(target);
    const attach = await get(box.started);
    await get(box.started, { since: attach.payload.cursor });
    const after = statSync(target);
    assert.equal(after.size, before.size, "只读端点不得改动事件文件大小");
    assert.equal(after.mtimeMs, before.mtimeMs, "只读端点不得触碰事件文件 mtime");
    const names = readdirSync(box.dir).sort();
    assert.deepEqual(names, ["dsh-11.jsonl"],
      "显式给了令牌就不落令牌文件：只读端点不得往桥目录里写任何东西");
  } finally {
    await box.done();
  }
});

test("默认令牌落盘：没给令牌时在桥目录建 lan-token.txt，服务用它鉴权", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "dsh-pet-lan-token-"));
  const started = await lan.startLanStream({ dir, port: 0 });
  try {
    const tokenFile = path.join(dir, lan.TOKEN_FILE_NAME);
    assert.ok(statSync(tokenFile).isFile(), "令牌文件必须落在桥目录");
    const onDisk = (await import("node:fs")).readFileSync(tokenFile, "utf8").trim();
    assert.equal(onDisk, started.token, "服务用的就是文件里那个令牌（用户照文件抄）");
    assert.ok(onDisk.length >= 24, "令牌不能短到可枚举");

    const denied = await fetch(`http://127.0.0.1:${started.port}/v1/events`);
    assert.equal(denied.status, 401, "没抄令牌的手机必须 401");
    const allowed = await fetch(`http://127.0.0.1:${started.port}/v1/events`, {
      headers: { authorization: `Bearer ${onDisk}` },
    });
    assert.equal(allowed.status, 200);
  } finally {
    await started.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("桥目录不存在时照常服务空事件流（DSH 还没启动）", async () => {
  const dir = path.join(mkdtempSync(path.join(tmpdir(), "dsh-pet-lan-empty-")), "missing");
  const started = await lan.startLanStream({ dir, token: "t", port: 0 });
  try {
    const response = await fetch(`http://127.0.0.1:${started.port}/v1/events`, { headers: { authorization: "Bearer t" } });
    assert.equal(response.status, 200, "目录缺失不是错误：手机应在 DSH 起来前也能连上");
    const payload = await response.json();
    assert.deepEqual(payload.records, []);
  } finally {
    await started.close();
    rmSync(path.dirname(dir), { recursive: true, force: true });
  }
});
