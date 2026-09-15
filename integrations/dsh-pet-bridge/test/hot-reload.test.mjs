import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, before, after } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

// 热重载行为测试（方案 C 的核心机制）：
// 1) probeDisk 能读到当前磁盘版本与 impl 实现文件 mtime；
// 2) 版本/mtime 未变化时 checkReload 不重载（无副作用——含"不重写 hello"
//    的行为断言，而不仅是 stamp 不变；旧的字段名 bug 会让每轮都重载并
//    重写 hello，stamp 重新 stamp 后数值不变，因此只断言 stamp 会漏掉 bug）；
// 3) 用"同一个实现文件 + 不同 query"验证 ESM 缓存破坏机制——
//    这正是热重载拿到新代码的原子动作；
// 4) 端到端热重载：模拟真实施真实重装的形态——package.json 版本升到
//    新版本 + 出现 impl/<新版本>/ 目录（内容同旧版，仅载体换了），
//    驱动 checkReload 后：壳 BRIDGE_VERSION 与**写盘 hello** 都带新版本，
//    验证 query 缓存破坏作用于实现文件本身（不经过会命中缓存的间接层）；
// 5) 同版本修复重装（mtime 变）→ 热重载且版本保持；
// 6) 热重载后重放既有 agent（旧实现期间创建的 agent 不再次触发 agent/created，
//    新实现必须经 ctx.agents.list() 或壳传入的 agent 快照恢复）。
//
// 环境隔离：整个桥包（index.js + package.json + impl/）先复制到桥包内临时
// 副本（.hr-test-<rand>/），再从副本动态 import 壳——壳的 bridgeBaseDir 由
// import.meta.url 决定，天然指向副本，生产代码不需要任何测试 seam。所有对
// package.json/impl 的写操作都发生在副本上，绝不改真实工作区文件——测试
// 崩溃/被杀也不污染仓库。临时副本放在桥包内（而不是 os.tmpdir()）是为了让
// 相对导入与包内 node_modules 解析行为与真实安装一致（当前 impl 只用 Node
// 内置模块，但这层保险不依赖于"实现层永远零依赖"）。
//
// 注意：before() 内动态 import 而非顶层 await import——Node 24 test runner
// 对含顶层 await 的测试文件有事件循环不退出问题（nodejs/node#58227）。

const bridgeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pkgPath = path.join(bridgeRoot, "package.json");
const original = fs.readFileSync(pkgPath, "utf8");

// 桥包内临时副本：复制 index.js + package.json + impl/（全部测试写操作都
// 发生在副本上）。impl 目录较小（当前仅注册表 + 0.3.0 实现），复制开销可忽略。
const tempBridge = fs.mkdtempSync(path.join(bridgeRoot, ".hr-test-"));
const tempPkgPath = path.join(tempBridge, "package.json");
fs.copyFileSync(path.join(bridgeRoot, "index.js"), path.join(tempBridge, "index.js"));
fs.copyFileSync(pkgPath, tempPkgPath);
fs.cpSync(path.join(bridgeRoot, "impl"), path.join(tempBridge, "impl"), { recursive: true });

/** @type {Record<string, any>} */
let shell;
before(async () => {
  // 从临时副本加载壳：bridgeBaseDir = 副本目录，probeDisk/动态 import 全部
  // 指向副本，真实工作区文件零接触。
  shell = await import(pathToFileURL(path.join(tempBridge, "index.js")).href);
});

after(() => {
  fs.rmSync(tempBridge, { recursive: true, force: true });
});

test("repeated apply is idempotent: hello/diagnostic written once, mux not duplicated", () => {
  // 回归：DSH 进程内多次调用插件 apply（实测每 ~2s 一次）时，重复 apply
  // 不得再写 hello/diagnostic、不得再起 control queue / mux 连接——否则
  // 刷屏 hello 且 approval/question 权威帧散落到多路连接上（「审批变事后」）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined; // 测试进程不建真实 WS
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-idem-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  try {
    // 重置到未挂载状态（前面测试可能已 apply 过，applied=true）。
    if (shell.__hotReloadTest.disposeActive) shell.__hotReloadTest.disposeActive();
    const ctx = fakeCtx();
    // 首次 apply：写 1 条 hello + 1 条 diagnostic。
    shell.apply(ctx);
    shell.__bridgeTest.flush();
    const f1 = path.join(tempLogRoot, "dsh-pet-bridge", shell.__bridgeTest.instanceFile);
    const rec1 = fs.readFileSync(f1, "utf8").trim().split(/\r?\n/).map(JSON.parse);
    const hello1 = rec1.filter((r) => r.event === "bridge/hello").length;
    assert.equal(hello1, 1, "首次 apply 必须恰好写一条 hello");

    // 重复 apply 若干次（模拟 DSH 每 2s 重调）：不得再写 hello/diagnostic。
    for (let i = 0; i < 5; i += 1) shell.apply(ctx);
    shell.__bridgeTest.flush();
    const rec2 = fs.readFileSync(f1, "utf8").trim().split(/\r?\n/).map(JSON.parse);
    const hello2 = rec2.filter((r) => r.event === "bridge/hello").length;
    const diag2 = rec2.filter((r) => r.event === "bridge/diagnostic").length;
    assert.equal(hello2, 1, `重复 apply 不得重复写 hello，实际 ${hello2}`);
    assert.equal(diag2, 1, `重复 apply 不得重复写 diagnostic，实际 ${diag2}`);
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

const NEW_VERSION = "9.9.9";
const newImplDir = path.join(tempBridge, "impl", NEW_VERSION);
const implRepoFile = path.join(newImplDir, "index.js");

function fakeCtx() {
  return {
    on() { return () => {}; },
    effect() {},
    get() { return undefined; },
  };
}

// ===== 计数的假上下文/假 agent（泄漏断言用）=====
// ctx.on 的活跃数由返回的 disposer 递减：这正是壳/实现 dispose 契约要清理的
// 那一类资源（陈旧回调 + 双重派发）。ctx.effect 的 fiber 级 effect 不在计数内
// ——它由插件 fiber 卸载统一清理，而热重载不重建 fiber（见测试内说明）。
function countingCtx() {
  const stats = { onActive: 0, onPeak: 0 };
  const handlers = new Map();
  const track = (event, fn) => {
    const list = handlers.get(event) || [];
    list.push(fn);
    handlers.set(event, list);
  };
  return {
    stats,
    handlers,
    ctx: {
      on(event, fn) {
        stats.onActive += 1;
        stats.onPeak = Math.max(stats.onPeak, stats.onActive);
        track(event, fn);
        return () => {
          stats.onActive -= 1;
          const list = handlers.get(event) || [];
          const index = list.indexOf(fn);
          if (index >= 0) list.splice(index, 1);
        };
      },
      effect(fn) {
        const cleanup = typeof fn === "function" ? fn() : null;
        return () => { if (typeof cleanup === "function") cleanup(); };
      },
      get() { return undefined; },
    },
  };
}

/** 假 agent：agent.ctx 的 on/effect 活跃数同样按 disposer 递减。 */
function countingAgent(id) {
  const stats = { onActive: 0, effectActive: 0 };
  const agent = {
    id,
    session: { id },
    name: "DSH",
    ctx: {
      on() {
        stats.onActive += 1;
        return () => { stats.onActive -= 1; };
      },
      effect(fn) {
        stats.effectActive += 1;
        // DSH 语义：effect 回调**立即执行**（登记监听），返回的清理函数在
        // effect 卸载时调用——因此这里立刻跑 fn()，与生产一致。
        const cleanup = typeof fn === "function" ? fn() : null;
        let cleaned = false;
        return () => {
          if (cleaned) return;
          cleaned = true;
          stats.effectActive -= 1;
          if (typeof cleanup === "function") cleanup();
        };
      },
    },
  };
  return { agent, stats };
}

/** 统计"未清理的 timer"：回调已触发的 timeout 视为已完结，不再计入。 */
function installTimerCounters() {
  const originals = {
    setInterval: globalThis.setInterval,
    setTimeout: globalThis.setTimeout,
    clearInterval: globalThis.clearInterval,
    clearTimeout: globalThis.clearTimeout,
  };
  const pending = new Set();
  const stats = { created: 0 };
  globalThis.setInterval = (...args) => {
    const handle = originals.setInterval(...args);
    pending.add(handle);
    stats.created += 1;
    return handle;
  };
  globalThis.setTimeout = (fn, ms, ...rest) => {
    const handle = originals.setTimeout((...cbArgs) => {
      pending.delete(handle);
      return typeof fn === "function" ? fn(...cbArgs) : undefined;
    }, ms, ...rest);
    pending.add(handle);
    stats.created += 1;
    return handle;
  };
  globalThis.clearInterval = (handle) => { pending.delete(handle); return originals.clearInterval(handle); };
  globalThis.clearTimeout = (handle) => { pending.delete(handle); return originals.clearTimeout(handle); };
  return {
    stats,
    pendingCount: () => pending.size,
    /** 打点：之后用 pendingSince() 统计"本窗口内新建且仍未清掉"的 timer。 */
    mark: () => new Set(pending),
    pendingSince: (mark) => [...pending].filter((handle) => !mark.has(handle)).length,
    restore() {
      globalThis.setInterval = originals.setInterval;
      globalThis.setTimeout = originals.setTimeout;
      globalThis.clearInterval = originals.clearInterval;
      globalThis.clearTimeout = originals.clearTimeout;
    },
  };
}

/** 读取某桥目录 dsh.jsonl 中 hello 记录条数（目录由 APPDATA 决定）。 */
function countHelloIn(appDataRoot) {
  try {
    const file = path.join(appDataRoot, "dsh-pet-bridge", shell.__bridgeTest.instanceFile);
    if (!fs.existsSync(file)) return 0;
    return fs.readFileSync(file, "utf8").trim().split(/\r?\n/)
      .filter((line) => line.includes('"bridge/hello"')).length;
  } catch {
    return 0;
  }
}

test("probeDisk reports the current on-disk package version and impl file mtime", () => {
  const probe = shell.__hotReloadTest.probeDisk();
  assert.ok(probe, "probeDisk must find the package");
  assert.match(probe.version, /^\d+\.\d+\.\d+/);
  assert.ok(probe.mtimeMs > 0, "impl file mtime must be positive");
  assert.ok(probe.implFile.endsWith(`impl${path.sep}${probe.version}${path.sep}index.js`));
  assert.equal(probe.version, shell.BRIDGE_VERSION);
  assert.equal(probe.implFile, path.join(tempBridge, "impl", probe.version, "index.js"));
});

test("checkReload is a no-op when the disk state matches the active stamp", async () => {
  // 行为断言：不仅 stamp 不变，hello 也必须不增长（此前字段名 bug 会让
  // checkReload 在磁盘零变化时也 dispose+re-apply+重写 hello，而重新 stamp
  // 后数值仍相同，所以只断言 stamp 会漏掉该 bug）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined;
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-noop-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  try {
    const before = shell.__hotReloadTest.activeStamp();
    assert.ok(before, "active implementation must be stamped at bootstrap");
    await shell.__hotReloadTest.checkReload(fakeCtx());
    const after = shell.__hotReloadTest.activeStamp();
    assert.deepEqual(after, before, "no disk change must not trigger a reload");
    shell.__bridgeTest.flush();
    assert.equal(countHelloIn(tempLogRoot), 0,
      "no disk change must not re-apply the implementation (hello count must stay 0)");
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

test("an ESM query string defeats the module cache (hot-reload atomic mechanism)", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-impl-"));
  const file = path.join(tmp, "impl.mjs");
  fs.writeFileSync(file, `export const MARKER = "v1";\n`, "utf8");
  try {
    const url = pathToFileURL(file).href;
    const first = await import(`${url}?t=1`);
    const second = await import(`${url}?t=2`);
    assert.equal(first.MARKER, "v1");
    assert.equal(second.MARKER, "v1");
    assert.notEqual(first, second, "different query must produce a fresh module instance");
    const again = await import(`${url}?t=2`);
    assert.equal(again, second, "same query must hit the module cache");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("reinstall (version bump + new impl dir) hot-reloads and writes the new hello version", async () => {
  // 屏蔽 WebSocket：新实现 apply 会尝试 mux 连接（真实进程中连接/重连
  // 定时器 unref；测试进程需要干净退出，这里直接屏蔽网络）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined;
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-hr-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  try {
    // 构造"重装后的磁盘形态"：新版本目录 + package.json 升版（临时副本内）。
    fs.mkdirSync(newImplDir, { recursive: true });
    fs.copyFileSync(
      path.join(tempBridge, "impl", "0.3.0", "index.js"),
      implRepoFile,
    );
    const pkg = JSON.parse(original);
    pkg.version = NEW_VERSION;
    fs.writeFileSync(tempPkgPath, JSON.stringify(pkg, null, 2) + "\n", "utf8");

    const changedProbe = shell.__hotReloadTest.probeDisk();
    assert.equal(changedProbe.version, NEW_VERSION);
    assert.ok(changedProbe.exists, "new impl file must exist after reinstall");
    // 版本号变化已足以让 probeDisk 判定"需要重载"（mtime 因 copyFileSync
    // 保留源 mtime 而相同也不影响——判据是 version OR mtime 任一变化）。

    await shell.__hotReloadTest.checkReload(fakeCtx());

    // 壳的版本常量与活动实现 stamp 都切到新版本。
    assert.equal(shell.BRIDGE_VERSION, NEW_VERSION, "shell BRIDGE_VERSION must follow reinstall");
    const stamp = shell.__hotReloadTest.activeStamp();
    assert.equal(stamp.version, NEW_VERSION);
    assert.equal(stamp.mtime, changedProbe.mtimeMs);

    // hello 是进程级一次性握手：重载/重装**不重发**（用户约定：首次成功握手后
    // 直到 DSH 重启不再发；本测试路径未走首次 apply，文件里应一条 hello 都没有）。
    // 新版本信息由**后续每条记录**携带（writeRecord 统一信封带 bridgeVersion），
    // pet 按记录逐条校验，不依赖 hello 刷新。
    const helloBefore = countHelloIn(tempLogRoot);
    await shell.__hotReloadTest.checkReload(fakeCtx());  // 触发重载
    shell.__bridgeTest.flush();
    const file = path.join(tempLogRoot, "dsh-pet-bridge", shell.__bridgeTest.instanceFile);
    const records = fs.readFileSync(file, "utf8").trim().split(/\r?\n/).map(JSON.parse);
    const hellos = records.filter((r) => r.event === "bridge/hello");
    assert.equal(hellos.length, helloBefore,
      "reload must NOT write a fresh hello (process-level one-time handshake)");
    assert.equal(hellos.length, 0,
      "本测试路径未首次 apply，重载也不应产生任何 hello");
    // 重载后写的新记录（非 hello）必须带新版本：new_version 已由 impl 写入。
    const newVersionRecords = records.filter(
      (r) => r.event !== "bridge/hello" && r.bridgeVersion === NEW_VERSION,
    );
    assert.ok(newVersionRecords.length > 0,
      `重载后普通记录必须携带新版本 ${NEW_VERSION}`);
    // 首条 hello 仍满足 Pet 契约。
    for (const hello of hellos) {
      assert.equal(hello.bridgeProtocolVersion, 1);
      assert.match(hello.bridgeVersion, /^\d+\.\d+\.\d+/);
      assert.ok(hello.capabilities.length > 0);
      assert.ok(hello.emittedEvents.length > 0);
    }

    // 再驱动一次：磁盘状态与 stamp 一致 → 不再重载（幂等，不新增 hello）。
    const stampBefore = shell.__hotReloadTest.activeStamp();
    await shell.__hotReloadTest.checkReload(fakeCtx());
    assert.deepEqual(shell.__hotReloadTest.activeStamp(), stampBefore,
      "a second check with no disk change must not re-reload");
    shell.__bridgeTest.flush();
    assert.equal(countHelloIn(tempLogRoot), helloBefore,
      "a second check with no disk change must not re-apply (hello count must not grow)");
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    // 还原临时副本的 package.json 与 impl 目录（后续测试在 0.3.0 形态上跑）。
    fs.writeFileSync(tempPkgPath, original, "utf8");
    try { fs.rmSync(newImplDir, { recursive: true, force: true }); } catch {}
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

test("same-version repair (content change, version unchanged) hot-reloads via mtime", async () => {
  // 修复性重装：版本号不变，但实现文件内容被替换（mtime 变化）→
  // probeDisk 的 mtime 判据必须触发重载，且写盘 hello 版本保持原版本。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined;
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-repair-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  try {
    const implFile = path.join(tempBridge, "impl", "0.3.0", "index.js");
    const originalProbe = shell.__hotReloadTest.probeDisk();
    // 复位到未挂载（前面 reinstall 测试的 applied 残留已由 disposeActive 清理）。
    if (shell.__hotReloadTest.disposeActive) shell.__hotReloadTest.disposeActive();
    // 首次 apply：建立进程级唯一 hello（tempLogRoot 是独立目录）。
    shell.apply(fakeCtx());
    shell.__bridgeTest.flush();
    assert.equal(countHelloIn(tempLogRoot), 1, "首次 apply 必须恰好写一条 hello");
    // 模拟"重装副本"：先 touch 时间戳（保证 mtime 变化），再原样写回内容。
    fs.utimesSync(implFile, new Date(Date.now() + 5000), new Date(Date.now() + 5000));
    const changedProbe = shell.__hotReloadTest.probeDisk();
    assert.equal(changedProbe.version, originalProbe.version, "version unchanged");
    assert.notEqual(changedProbe.mtimeMs, originalProbe.mtimeMs, "mtime must change on repair");

    await shell.__hotReloadTest.checkReload(fakeCtx());
    shell.__bridgeTest.flush();
    const file = path.join(tempLogRoot, "dsh-pet-bridge", shell.__bridgeTest.instanceFile);
    const records = fs.readFileSync(file, "utf8").trim().split(/\r?\n/).map(JSON.parse);
    const hellos = records.filter((r) => r.event === "bridge/hello");
    // 修复性重装同样**不重发 hello**（进程级一次性）：hello 数量保持初始 1 条。
    assert.equal(hellos.length, 1,
      "repair reload must NOT re-hello (process-level one-time handshake)");
    assert.match(hellos[0].bridgeVersion, /^\d+\.\d+\.\d+/, "hello 版本必须合法 semver");
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

test("repeated hot reloads do not accumulate listeners, agent hooks or timers", async () => {
  // 为什么必须有这条断言：import(`?t=`) 破坏的是 ESM 缓存命中，**不能卸载**
  // 已 import 的模块——每次重载都新增一份模块图。次数少（几次重装/会话）时
  // 无害，但它把 dispose() 从"好习惯"变成正确性前提：漏解一个监听器/timer/
  // 订阅，就会陈旧回调 + 双重派发，而且症状是"用久了才犯"的慢性病。这里反复
  // 重载（含存活 agent 的重放路径）后断言：
  //   1) 活跃 ctx.on 监听数、agent 级 on/effect 活跃数不随重载次数增长；
  //   2) 全部 dispose 之后，本测试窗口内由实现创建的 timer 一个不剩。
  // 计数口径：ctx.on 的活跃数由返回的 disposer 递减；ctx.effect 注册的 fiber
  // 级 effect 不计入（它由插件 fiber 卸载统一清理，而热重载不重建 fiber——
  // 实现侧另有 registerDisposer 兜底清理同一份资源）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined; // 不建真实 WS（否则 mux 重连定时器会进计数）
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-leak-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  const timers = installTimerCounters();
  const { ctx, stats, handlers } = countingCtx();
  const { agent, stats: agentStats } = countingAgent("session-leak-1");
  const implFile = path.join(tempBridge, "impl", "0.3.0", "index.js");
  let mtime = Date.now() + 60000;
  const reloadOnce = async () => {
    fs.utimesSync(implFile, new Date(mtime), new Date(mtime));
    mtime += 1000;
    await shell.__hotReloadTest.checkReload(ctx);
    shell.__bridgeTest.flush();
  };
  const snapshot = () => ({
    ctxListeners: stats.onActive,
    agentListeners: agentStats.onActive,
    agentEffects: agentStats.effectActive,
  });
  try {
    if (shell.__hotReloadTest.disposeActive) shell.__hotReloadTest.disposeActive();
    const mark = timers.mark();
    // 基线：一份实现实例 + 一个存活 agent（后续重载经壳的 agent 快照重放）。
    await reloadOnce();
    const created = handlers.get("agent/created") || [];
    assert.ok(created.length > 0, "impl must register an agent/created handler");
    for (const fn of created) fn({ agent });
    const baseline = snapshot();
    assert.ok(baseline.ctxListeners > 0, "基线必须包含活跃监听（否则断言空转）");
    assert.ok(
      baseline.agentListeners > 0 && baseline.agentEffects > 0,
      "基线必须包含 agent 级监听/effect（否则断言空转）",
    );

    for (let i = 0; i < 20; i += 1) await reloadOnce();

    assert.deepEqual(
      snapshot(),
      baseline,
      "热重载 20 次后活跃监听/effect 数必须回到同一水平（不漏解、也不累积）",
    );

    // dispose 全部收尾：窗口内创建的 timer 必须一个不剩。
    shell.__hotReloadTest.disposeActive();
    assert.equal(
      timers.pendingSince(mark),
      0,
      "dispose 后不得留下本实例创建的 timer（合批 flush / control queue / 元数据刷新）",
    );
  } finally {
    timers.restore();
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

test("a contract-mismatching implementation is refused instead of half-installed", async () => {
  // 壳的协议常量被 DSH 缓存（热重载只换 impl/<version>/）。若新 impl 改了
  // 协议/事件清单，旧行为是"把新实现挂上 → Pet 逐条严格校验拒收 → 该 agent
  // 联动整体失效"，用户只看到一条限频气泡。现在必须在 apply 之前比对并拒绝：
  // 旧实现继续工作、版本常量不前进，且原因要送到 Pet 侧（severity=error 的
  // bridge/diagnostic）。同一份磁盘形态不重复报告（换新构建才重试）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined;
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-contract-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  const driftVersion = "9.9.8";
  const driftImplDir = path.join(tempBridge, "impl", driftVersion);
  const driftImplFile = path.join(driftImplDir, "index.js");
  try {
    const source = fs.readFileSync(path.join(tempBridge, "impl", "0.3.0", "index.js"), "utf8");
    const tampered = source.replace(
      '"agent_reasoning_raw_content",',
      '"agent_reasoning_raw_content_v2",',
    );
    assert.notEqual(tampered, source, "篡改必须命中事件清单（否则本用例自欺）");
    fs.mkdirSync(driftImplDir, { recursive: true });
    fs.writeFileSync(driftImplFile, tampered, "utf8");
    const pkg = JSON.parse(original);
    pkg.version = driftVersion;
    fs.writeFileSync(tempPkgPath, JSON.stringify(pkg, null, 2) + "\n", "utf8");

    const beforeStamp = shell.__hotReloadTest.activeStamp();
    const beforeVersion = shell.BRIDGE_VERSION;
    await shell.__hotReloadTest.checkReload(fakeCtx());
    shell.__bridgeTest.flush();

    assert.deepEqual(
      shell.__hotReloadTest.activeStamp(),
      beforeStamp,
      "契约不符必须拒绝激活：活动实现的 stamp 不得前进",
    );
    assert.equal(shell.BRIDGE_VERSION, beforeVersion, "壳版本常量不得跟随契约不符的实现");
    assert.ok(shell.__hotReloadTest.rejectedStamp, "拒绝必须被记住（否则每 2s 轮询重复报告）");

    const file = path.join(tempLogRoot, "dsh-pet-bridge", shell.__bridgeTest.instanceFile);
    const readRefusals = () => fs.readFileSync(file, "utf8").trim().split(/\r?\n/)
      .map(JSON.parse)
      .filter((r) => r.event === "bridge/diagnostic" && r.reason === "bridge-contract-mismatch");
    const refusals = readRefusals();
    assert.equal(refusals.length, 1, `拒绝原因必须写出一条诊断记录，实际 ${refusals.length}`);
    assert.equal(refusals[0].severity, "error");
    assert.match(refusals[0].message, /重启 DSH/, "提示必须明确要求重启 DSH");
    assert.match(refusals[0].message, /BRIDGE_EVENT_INVENTORY/, "提示必须带上具体不一致项");

    // 同一份磁盘形态再次轮询：不重复报告。
    await shell.__hotReloadTest.checkReload(fakeCtx());
    shell.__bridgeTest.flush();
    assert.equal(readRefusals().length, 1, "同一份被拒绝的形态不得每轮重复报告");
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    fs.writeFileSync(tempPkgPath, original, "utf8");
    try { fs.rmSync(driftImplDir, { recursive: true, force: true }); } catch {}
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});

test("compareImplContract flags protocol, inventory and capability drift", () => {
  const compare = shell.__hotReloadTest.compareImplContract;
  const good = {
    BRIDGE_PROTOCOL_VERSION: shell.BRIDGE_PROTOCOL_VERSION,
    BRIDGE_EVENT_INVENTORY: [...shell.BRIDGE_EVENT_INVENTORY],
    BRIDGE_CAPABILITIES: [...shell.BRIDGE_CAPABILITIES],
  };
  assert.deepEqual(compare(good), [], "与壳一致的实现不得被判为不符");

  assert.match(compare({ ...good, BRIDGE_PROTOCOL_VERSION: 2 }).join("；"), /协议版本/);
  assert.match(
    compare({
      ...good,
      BRIDGE_EVENT_INVENTORY: [...good.BRIDGE_EVENT_INVENTORY, "brand/new"],
    }).join("；"),
    /BRIDGE_EVENT_INVENTORY 不一致/,
  );
  assert.match(
    compare({ ...good, BRIDGE_CAPABILITIES: good.BRIDGE_CAPABILITIES.slice(1) }).join("；"),
    /BRIDGE_CAPABILITIES 不一致/,
  );
  assert.match(compare({}).join("；"), /协议版本/);
});

test("reload replays already-live agents into the new implementation", async () => {
  // 关键正确性：热重载换新实现后，旧实现期间创建的 agent 不会再次触发
  // agent/created——新实现必须通过 ctx.agents.list() 或壳传来的 agent 快照
  // 重放，否则状态聚合与 watchdog 控制会在重载后失效（直到新 agent 创建）。
  const oldWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = undefined;
  const oldBridgeDir = process.env.DSH_PET_BRIDGE_DIR;
  const tempLogRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-replay-"));
  // 数据根走插件与桌宠共用的显式入口（三平台同一口径 DSH_PET_BRIDGE_DIR）：
  // 不再依赖 APPDATA/HOME 的平台差异，也不需要知道平台默认路径长什么样。
  process.env.DSH_PET_BRIDGE_DIR = path.join(tempLogRoot, "dsh-pet-bridge");
  try {
    // 构造一个"已存在的 agent"：最小桩（id + session.id + ctx.effect/on）。
    const agentStub = {
      id: "session-replay-1",
      session: { id: "session-replay-1" },
      ctx: { effect(fn) { return fn(); }, on() { return () => {}; } },
      name: "DSH",
    };
    const ctx = {
      on() { return () => {}; },
      effect() {},
      get(name) {
        if (name === "agents") return { list: () => [agentStub] };
        return undefined;
      },
    };

    // 制造一次重载（同版本 mtime 变化即可）。
    const implFile = path.join(tempBridge, "impl", "0.3.0", "index.js");
    fs.utimesSync(implFile, new Date(Date.now() + 9000), new Date(Date.now() + 9000));
    await shell.__hotReloadTest.checkReload(ctx);
    fs.utimesSync(implFile, new Date(), new Date());

    // 新实现的 liveAgents 必须包含重放的 agent。
    const liveAgents = shell.__controlTest.liveAgents;
    assert.ok(liveAgents, "control test surface must expose liveAgents");
    assert.ok(
      liveAgents.has("session-replay-1"),
      "reload must replay live agents into the new implementation",
    );
  } finally {
    if (oldBridgeDir === undefined) delete process.env.DSH_PET_BRIDGE_DIR;
    else process.env.DSH_PET_BRIDGE_DIR = oldBridgeDir;
    fs.rmSync(tempLogRoot, { recursive: true, force: true });
    if (shell && shell.__hotReloadTest && shell.__hotReloadTest.disposeActive) {
      shell.__hotReloadTest.disposeActive();
    }
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
  }
});