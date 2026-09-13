// dsh-pet 桌宠桥接插件：稳定壳（热重载入口）。
//
// 职责划分：
// - 本文件（壳）：只做三件事——声明协议契约常量、把 DSH 插件生命周期
//   （apply/inject）转发给"当前活跃实现"、按磁盘版本变化热重载实现。
// - impl/<semver>/index.js：真正的业务逻辑（事件转发、jsonl 写盘、诊断、
//   watchdog 控制、mux 中继）。每个版本一个目录，壳按语义版本选择最高者。
//
// 热重载机制：
// 1) 每次轮询（RELOAD_POLL_MS）扫描本包 impl/ 目录，取最高语义版本；
// 2) 若磁盘最高版本或其实例文件 mtime 与已加载实现不一致 → 需要重载；
// 3) 重载时先 dispose 旧实现（解除监听、清 timer、关 mux、flush 收尾），
//    再用带 `?t=<mtime>` 的 URL 动态 import 新实现（query 破坏 ESM 模块缓存），
//    最后调用新实现的 apply(ctx) —— 它会立即重写 bridge/hello（带新 BRIDGE_VERSION），
//    桌宠端 tailer 读到新 hello，validate_bridge_record 校验通过，气泡自动消失。
//    （Pet 契约只强校验协议版本/事件清单/capabilities，bridgeVersion 只要合法
//    semver 即可——见 pet/bridge_contract.py；因此热重载后版本号变化不会误报。）
//
// 这样桌宠端"卸载重装 bridge"（替换磁盘 junction 内容）不用重启 DSH 即可生效。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// 版本用 fs.readFileSync 直读（不用 require 系机制）：既满足
// verify_import 的「零依赖 + 禁 CommonJS 逃逸口」红线，又保证读到磁盘当前
// 内容（CJS require(package.json) 会命中 require.cache，跨热重载实例共享
// 旧版本——已实测抓出的 bug，见 impl 同款注释）。
const PACKAGE_VERSION = String(
  (() => {
    try {
      return JSON.parse(
        fs.readFileSync(new URL("./package.json", import.meta.url), "utf8"),
      ).version || "";
    } catch {
      return "";
    }
  })(),
);

// ===== 协议契约（单一真相来源，Python/Node 契约测试按文本解析这些导出） =====
// 协议版本独立于包 semver：改记录信封才 bump 协议；修实现只升包版本。
export const BRIDGE_PROTOCOL_VERSION = 1;

// 声明为 let：热重载到新版本包时更新为磁盘 package.json 的版本，让 hello
// 记录携带当前包的真实版本。初始值与本包 package.json 一致。
export let BRIDGE_VERSION = PACKAGE_VERSION;

// 事件清单与能力清单是协议常量：任何版本实现都必须与 Pet 侧完全一致，
// 否则桥接无效（这是协议设计，不是 bug）。热重载只换实现不换契约。
export const BRIDGE_EVENT_INVENTORY = Object.freeze([
  "AgentStatus",
  "agent/request-error",
  "agent_reasoning",
  "agent_reasoning_raw_content",
  "approval/asked",
  "approval/decided",
  "approval/request",
  "approval/resolved",
  "assistant/message",
  "bridge/control-received",
  "bridge/control-result",
  "bridge/diagnostic",
  "bridge/hello",
  "command/done",
  "command/run",
  "context_compacted",
  "cordis/request-run",
  "cordis/request-run-resolved",
  "execution/failed",
  "exec_command_begin",
  "exec_command_end",
  "interaction/resolved",
  "llm/retry",
  "llm_error",
  "mcp_tool_call_begin",
  "mcp_tool_call_end",
  "model_access",
  "question/requested",
  "question/resolved",
  "step/end",
  "step/start",
  "task_complete",
  "task_started",
  "thread_rolled_back",
  "tool-workflow/run-end",
  "tool-workflow/run-start",
  "tool/call",
  "tool/result",
  "turn/end",
  "turn/start",
  "user/message",
  "user_action",
  "watchdog/control-result",
  "web_search_begin",
  "web_search_end",
]);

export const BRIDGE_CAPABILITIES = Object.freeze([
  "agent-status",
  "session-events",
  "tool-events",
  "interaction-relay",
  "watchdog-control",
  "model-access-diagnostics",
]);

// ===== 热重载基础设施 =====
const RELOAD_POLL_MS = 2000; // 磁盘版本探测间隔（桌宠重装 bridge 后 ≤2s 生效）
const bridgeBaseDir = path.dirname(fileURLToPath(import.meta.url));
const packageJsonPath = path.join(bridgeBaseDir, "package.json");

// 静态挂载首版实现（通过 impl/index.js 版本注册表）。ESM 静态循环在"绑定
// 只在函数体内读取"时安全：impl 从 package.json 自读版本，不反向依赖壳，
// 因此不存在 require(esm) 的循环限制。测试面在 import 后立即可用。
// 注意：热重载**不会**经 impl/index.js 间接层（其 export * 相对导入不带
// query，会命中缓存）——热重载直接动态 import 目标版本的实现文件。
import * as initialImpl from "./impl/index.js";

// 当前活跃实现模块。初始即 initialImpl；热重载换新版本。
let activeImpl = initialImpl;
let pollTimer = null;
// 活动实现的加载标记（版本/mtime）。ESM namespace 不可扩展，不能写属性，
// 因此由壳在模块级持有（activeStamp / checkReload 对比用）。
let activeVersion = "";
let activeMtime = 0;

// 探测磁盘当前实现文件：返回
//   { version, implFile, mtimeMs, exists }
// 其中 implFile = impl/<package.json version>/index.js（重装 bridge 时该
// 文件内容或 package.json 版本变化，至少其一触发重载）。
function probeDisk() {
  try {
    const pkg = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
    const version = String(pkg.version || "");
    const implFile = path.join(bridgeBaseDir, "impl", version, "index.js");
    let mtimeMs = 0;
    let exists = false;
    try { mtimeMs = fs.statSync(implFile).mtimeMs; exists = true; } catch {}
    return { version, implFile, mtimeMs, exists };
  } catch {
    return null;
  }
}

// 记录活动实现的加载标记（壳模块级持有，ESM namespace 不可扩展）。
function stampImpl(mod, probe) {
  activeVersion = probe.version;
  activeMtime = probe.mtimeMs;
}

function activeStamp() {
  if (!activeImpl) return null;
  return {
    version: activeVersion,
    mtime: activeMtime,
  };
}

// 模块加载时同步挂载首版（只加载不 apply；apply 由 DSH 调用）。
(function bootstrap() {
  const probe = probeDisk();
  if (probe) {
    stampImpl(initialImpl, probe);
    BRIDGE_VERSION = probe.version;
  }
})();

// 热重载时从旧实现收集存活 agent 快照：发布版 DSH 可能没有 ctx.agents 服务
// （见 impl apply 的重放注释），若缺失，新实现用快照兜底重放，否则热重载后
// 既有 agent 的状态聚合/控制全部丢失。快照是 agent 对象引用（跨模块实例共享
// 同一批 DSH 对象，可直接复用，不存在序列化问题）。
function snapshotAgents(mod) {
  try {
    const live = mod?.__controlTest?.liveAgents;
    if (!live || !(live instanceof Map) || live.size === 0) return null;
    return [...live.values()].filter(Boolean);
  } catch {
    return null;
  }
}

// 异步加载目标实现并挂载（热重载专用）。query 直接加在实现文件 URL 上：
// Node 对带不同 query 的 URL 视为不同模块，绕过 loadCache，保证拿到新代码。
// 顺序（swap 语义，避免窗口期双写）：
//   1) 纯加载新模块——失败（语法错/文件损坏/缺 apply）直接抛，旧实现保持可用，
//      下轮轮询带着同一 probe 重试（自愈）；
//   2) 加载成功后才 dispose 旧实现，再 apply 新实现；
//   3) apply 失败（新版本自身 bug）→ 清理半初始化的新实例后抛，stamp 不更新，
//      下轮轮询继续尝试，绝不静默卡死在半挂载状态。
async function loadImplementationAsync(probe, ctx, inheritedAgents = null) {
  const url = pathToFileURL(probe.implFile).href;
  const mod = await import(`${url}?t=${probe.mtimeMs}`);
  if (!mod || typeof mod.apply !== "function") {
    throw new Error(`impl ${probe.version} has no apply export`);
  }
  if (typeof activeImpl.dispose === "function") {
    try { activeImpl.dispose(); } catch {}
  }
  try {
    // 热重载/重装**不重发 hello**（进程级一次性握手；新版本由后续记录携带）。
    mod.apply(ctx, inheritedAgents, { skipHello: true });
  } catch (err) {
    try { if (typeof mod.dispose === "function") mod.dispose(); } catch {}
    throw err;
  }
  stampImpl(mod, probe);
  activeImpl = mod;
  BRIDGE_VERSION = probe.version;
  return mod;
}

async function checkReload(ctx) {
  try {
    const probe = probeDisk();
    if (!probe || !activeImpl || !probe.exists) return;
    const stamp = activeStamp();
    // 注意字段名：probeDisk 返回的是 mtimeMs。`probe.mtime` 不存在，恒为
    // undefined，导致该比较永远 false → 磁盘零变化也每轮触发热重载
    // （dispose+re-apply+重写 hello），这是 P0 实测 bug，必须用 mtimeMs 比较。
    if (stamp && probe.version === stamp.version && probe.mtimeMs === stamp.mtime) return;
    // 磁盘变化：先取旧实例的 agent 快照（供新实例无 agents 服务时兜底重放），
    // 再进入 swap 加载（dispose 在 loadImplementationAsync 内部、新实现挂好后才切换）。
    const inherited = snapshotAgents(activeImpl);
    await loadImplementationAsync(probe, ctx, inherited);
  } catch (err) {
    console.warn(`[dsh-pet-bridge] hot-reload failed: ${String(err?.message || err)}`);
  }
}

function startReloadPolling(ctx) {
  if (pollTimer || !ctx) return;
  pollTimer = setInterval(() => {
    checkReload(ctx);
  }, RELOAD_POLL_MS);
  if (pollTimer.unref) pollTimer.unref();
  ctx.effect?.(() => () => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }, "dsh-pet-bridge.reload-poll()");
}

// 已挂载的 ctx 引用：DSH 插件管理器可能对同一插件重复 apply（例如它自身的
// 重载逻辑），每次都重挂 ctx.on 会双注册监听、双写盘。同一 ctx 只挂载一次；
// 热重载换实现走 loadImplementationAsync（不经过壳 apply），不受此锁影响。
let appliedCtx = null;

export function apply(ctx) {
  if (appliedCtx && appliedCtx === ctx) {
    // 同一 ctx 重复 apply：监听已经挂好，忽略（避免双注册双写）。
    return;
  }
  appliedCtx = ctx;
  // 挂载当前实现的事件监听，并启动热重载轮询。首版已由模块 bootstrap 挂载。
  if (activeImpl && typeof activeImpl.apply === "function") {
    try { activeImpl.apply(ctx); } catch (err) {
      console.warn(`[dsh-pet-bridge] impl apply failed: ${String(err?.message || err)}`);
    }
  }
  startReloadPolling(ctx);
  // 插件 fiber 卸载时（DSH 重启/卸载插件）同步清理当前实现的资源。
  ctx.effect?.(() => () => disposeActive(), "dsh-pet-bridge.dispose()");
}

// 清理当前活跃实现的全部资源（监听、定时器、mux）。热重载与插件卸载共用。
function disposeActive() {
  if (activeImpl && typeof activeImpl.dispose === "function") {
    try { activeImpl.dispose(); } catch { /* 清理失败不阻断卸载/热重载 */ }
  }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// 协议注入服务集（DSH 加载插件时据此注入）。这是协议常量，不随实现版本变。
export const inject = ["llm", "agentDefaultModel"];

// 测试面转发：Node 契约测试 import 壳的 __bridgeTest / __controlTest /
// __retryTest / __hardFailureTest。它们从实现模块取值；实现未加载时返回空壳。
function implExports() {
  return activeImpl || {};
}
export const __controlTest = new Proxy({}, {
  get: (_, key) => implExports().__controlTest?.[key],
  has: (_, key) => key in (implExports().__controlTest || {}),
});
export const __messageTest = new Proxy({}, {
  get: (_, key) => implExports().__messageTest?.[key],
  has: (_, key) => key in (implExports().__messageTest || {}),
});
export const __questionTest = new Proxy({}, {
  get: (_, key) => implExports().__questionTest?.[key],
  has: (_, key) => key in (implExports().__questionTest || {}),
});
export const __retryTest = new Proxy({}, {
  get: (_, key) => implExports().__retryTest?.[key],
  has: (_, key) => key in (implExports().__retryTest || {}),
});
export const __hardFailureTest = new Proxy({}, {
  get: (_, key) => implExports().__hardFailureTest?.[key],
  has: (_, key) => key in (implExports().__hardFailureTest || {}),
});
export const __bridgeTest = new Proxy({}, {
  get: (_, key) => implExports().__bridgeTest?.[key],
  has: (_, key) => key in (implExports().__bridgeTest || {}),
});

// 热重载测试 seam：暴露探测/重载状态，供 hot-reload.test.mjs 驱动
// （验证"磁盘版本变化 → checkReload → 切换到新实现"的完整路径）。
export const __hotReloadTest = {
  probeDisk,
  activeStamp,
  checkReload,
  disposeActive,
  packageJsonPath,
  get activeApplied() {
    return Boolean(activeImpl);
  },
};