// dsh-pet 桌宠桥接：版本化实现层（impl/0.3.0）。
// 本文件由壳（../../index.js）经 impl/index.js 挂载；壳负责版本探测与热重载。
// 契约常量在这里自持一份副本（协议版本/事件清单/能力清单），与壳及 Pet 侧
// 的锁步由 tests/test_bridge_contract_consumer.py 双向校验保证——热重载时
// 新版本若改动契约，Pet 侧会拒绝 hello（这正是契约设计的意图）。
//
// 实现层订阅 DSH 的 agent 生命周期事件，追加写入共享桥目录的 dsh-{pid}.jsonl，
// 桌宠侧的 DshMonitor 通过 byte-offset tail 读取（不回放历史）。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { randomUUID } from "node:crypto";

// ===== 零依赖红线（与壳 index.js 同步，来自 main 的 7f3b896） =====
// 本插件必须保持零外部依赖：profile 经 pnpm 的 link: 协议链接到本目录，
// pnpm 不会安装被链接包自己的依赖；而链接目标常常是打包版桌宠
// _internal 内的副本（CI 构建不带 node_modules）。一旦此处声明运行时依赖，
// 依赖解析失败会让 Cordis 插件树初始化整体抛错、DSH 无法启动（2026-09 事故：
// 作者与多用户 dsh 全 profile 起不来）。因此 user-message envelope 手写，
// 形状与 @deepseek-ai/dsh-llm 的 createUserMessage 完全对齐——
// {...input, role: "user", id: randomUUID()}，structuredClone 后深冻结。
// dsh 升级 envelope 形状时这里必须同步（inject 的 llm.stream / steer 消费它）。
function deepFreezeMessage(value, seen = new WeakSet()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return value;
  seen.add(value);
  for (const key of Object.keys(value)) deepFreezeMessage(value[key], seen);
  return Object.freeze(value);
}

// dsh createUserMessage 的本地等价物：补齐 role/id，返回不可变快照。
function createUserMessage(input) {
  const message = structuredClone({ ...input, role: "user", id: randomUUID() });
  return deepFreezeMessage(message);
}

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）
const PLUGIN_ID = "dsh-pet-bridge";
const BRIDGE_PROTOCOL_VERSION = 1;
// 版本必须每个模块实例独立读取：CJS 的 require(package.json) 会命中
// require.cache（跨热重载实例共享），重装后新实现会读到旧版本号。
// 用 readFileSync + JSON.parse 保证读到当前磁盘内容。
const BRIDGE_VERSION = String(
  (() => {
    try {
      const pkgPath = fileURLToPath(new URL("../../package.json", import.meta.url));
      return JSON.parse(fs.readFileSync(pkgPath, "utf8")).version || "";
    } catch {
      return "";
    }
  })(),
);

// 协议事件清单/能力清单（与壳字面量一致；契约测试双向校验）。
const BRIDGE_EVENT_INVENTORY = Object.freeze([
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
const BRIDGE_CAPABILITIES = Object.freeze([
  "agent-status",
  "session-events",
  "tool-events",
  "interaction-relay",
  "watchdog-control",
  "model-access-diagnostics",
]);
export { BRIDGE_PROTOCOL_VERSION, BRIDGE_VERSION, BRIDGE_EVENT_INVENTORY, BRIDGE_CAPABILITIES };

const inject = ["llm", "agentDefaultModel"];
const CONTROL_POLL_MS = 150;
const CONTROL_MAX_CONTEXT = 12000;
const CONTROL_MAX_REQUEST_AGE_MS = 10 * 60 * 1000;

// 进程内状态去重 + 多 Agent 聚合：
// 1) dsh 在 agent 创建/状态切换瞬间会抖动出重复 idle（实测 idle→working 仅隔
//    4ms），重复聚合状态不落盘——否则桌宠端 2 秒换帧节流会吞掉真实 working。
// 2) 必须按 agent 分别跟踪再聚合（任一在忙 = 忙）：dsh 可并发多个 agent
//   （子代理/多会话），全局单值去重会让先完成的 agent 把还在干活的顶成 idle。
const agentStates = new Map(); // agent 对象 → "working" | "idle"
const liveAgents = new Map(); // agent/session id → agent object
const knownSessions = new Set();
const sessionMetaCache = new Map(); // sessionId → { sessionName, projectName, agentName }
let metadataRefreshPromise = null;
let metadataRefreshTimer = null;
let lastState = null;
// apply 幂等标志：DSH 进程内可能多次调用插件 apply（实测每 ~2s 一次），
// 重复 apply 不得重复启动 watchdog 轮询/mux/监听（见 startControlQueue /
// muxConnect 的防重入）。dispose() 时复位，允许热重载后的新实例重新挂载。
let applied = false;
let controlQueueStarted = false;
let controlQueueTimer = null;

// ===== dispose 登记（供壳热重载调用） =====
// 热重载时壳先调用本模块的 dispose() 再挂载新实现。这里登记所有通过
// ctx.on / ctx.effect / setInterval / WebSocket 注册的清理句柄，确保旧实例
// 的解绑、清 timer、关 mux 全部执行，不残留重复监听或悬挂资源。
const disposeHandlers = new Set();
function registerDisposer(fn) {
  if (typeof fn === "function") disposeHandlers.add(fn);
  return fn;
}

export function dispose() {
  for (const fn of [...disposeHandlers]) {
    try { fn(); } catch { /* 清理失败不阻断热重载 */ }
  }
  disposeHandlers.clear();
  agentStates.clear();
  liveAgents.clear();
  knownSessions.clear();
  sessionMetaCache.clear();
  lastState = null;
  // 复位 apply 幂等标志：热重载后的新实例必须能完整重新挂载
  // （新的 control queue / mux / 监听）。
  applied = false;
  controlQueueStarted = false;
  controlQueueTimer = null;
  if (metadataRefreshTimer) { clearTimeout(metadataRefreshTimer); metadataRefreshTimer = null; }
  metadataRefreshPromise = null;
  flushPending(); // 收尾：把队列里还没落盘的记录写出去
}

function aggregateWrite() {
  const anyBusy = [...agentStates.values()].some((s) => s === "working");
  const next = anyBusy ? "working" : "idle";
  if (next === lastState) return;
  lastState = next;
  writeRecord({ state: next });
}

// 连接/超时类失败错误码（与下方 isModelAccessError 共用；DSH 的 llm/retry 里
// 错误码不统一，消息必含超时或连接断词，码+消息两者归一判定）。
const MODEL_ACCESS_CONN_CODES = new Set([
  "TIMEOUT", "REQUEST_TIMEOUT", "UPSTREAM_TIMEOUT", "ETIMEDOUT",
  "ESOCKETTIMEDOUT", "ECONNABORTED", "ECONNRESET", "ECONNREFUSED",
  "EPIPE", "EAI_AGAIN", "ENETUNREACH", "EHOSTUNREACH", "NETWORK_ERROR",
]);

// 判定是否为模型访问失败（服务端限流/过载，或上游响应连接/超时类故障）。
// DSH 实测 errorCode 为 "RATE_LIMIT"（消息如 "429: ..."），偶见直接 "429"；
// 网络类故障常见 errorCode 为 "TIMEOUT"/"ETIMEDOUT" 等（消息形如
// "upstream stream read failed before completion: upstream response headers
// timed out before streaming started"）。连接/超时与限流同样属于「本次模型
// 请求未成功、进入重试链」的异常，累计到阈值后也必须提醒桌宠（见下方
// RETRY_EVENT_THRESHOLD 注释）。必须同时匹配 code 与 message，避免漏判。
function isModelAccessError(code, message) {
  const c = String(code || "").trim().toUpperCase();
  const m = String(message || "");
  if (c === "RATE_LIMIT" || c === "429" || c === "TOO_MANY_REQUESTS") return true;
  if (m.startsWith("429") || /\b429\b/.test(m) || /rate.?limit/i.test(m)) return true;
  if (MODEL_ACCESS_CONN_CODES.has(c)) return true;
  return /\btimed?\s?out\b|timed out before|connection (reset|refused|aborted|closed|reset by peer)|network (error|unreachable|is unreachable)|socket hang up|eai_again|read ?ec 0|econnreset|etimedout/i.test(m);
}

// 桥目录必须与桌宠端一致：win32=%APPDATA%，darwin=~/Library/Application Support，其他=~/.config
function bridgeDir() {
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || os.homedir(), "dsh-pet-bridge");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "dsh-pet-bridge");
  }
  return path.join(os.homedir(), ".config", "dsh-pet-bridge");
}

function controlRequestPath(id) {
  return path.join(bridgeDir(), `watchdog-request-${id}.json`);
}

function controlResponsePath(id) {
  return path.join(bridgeDir(), `watchdog-response-${id}.json`);
}

function writeControlResponse(id, result) {
  try {
    fs.mkdirSync(bridgeDir(), { recursive: true });
    fs.writeFileSync(controlResponsePath(id), JSON.stringify({
      id, ts: Date.now(), ...result,
    }), "utf8");
  } catch (err) {
    // The response file is a convenience for the pet.  Never affect the Agent.
  }
}

function controlAgent(ctx, sessionId) {
  const id = String(sessionId || "");
  if (!id) return null;
  const live = liveAgents.get(id);
  if (live) return live;
  try {
    return ctx?.agents?.get?.(id) || null;
  } catch (err) {
    // DSH's strict injection proxy may throw when the optional service is not
    // declared. Unknown sessions must still converge to a normal not-found.
    console.warn(`[${PLUGIN_ID}] agents lookup unavailable: ${String(err?.message || err)}`);
    return null;
  }
}

function agentBelongsToLiveSession(agent, sessionId) {
  if (!agent) return false;
  const id = String(sessionId);
  return liveAgents.get(id) === agent ||
    liveAgents.get(String(agent.id || "")) === agent ||
    liveAgents.get(String(agent.session?.id || "")) === agent;
}

function controlAgentState(agent) {
  return String(agent?.status || agent?.state || "unknown");
}

// ===== 子代理 → 根会话归一 =====
// DSH 的会话在持久化 header 里携带谱系：parentSession（直接父会话 id）、
// delegationDepth（顶层为 0/缺省，子代理 = 父级深度 + 1）、origin === "subagent"
// （直接子代理标记）。运行时 Agent 经 session.header 暴露该 header。
// 控制动作（interrupt/replan）打在一个子代理上时，主 agent 会立刻补派新的
// 子代理——用户视角「终止没用」。因此把控制归一到目标会话的根会话：
//   子代理链上的 agent 统一作用到其最高可解析的存活祖先（根）；
//   顶层 session 直接作用自身。
// 返回的对象同时给出 wasSubagent / appliedToRoot / rootSessionId / subagentChain，
// 供 pet 侧区分「已终止会话（含子代理）」与「已终止子代理（主代理仍在运行）」。
function resolveControlRoot(agent, sessionLookup) {
  const sessionIdOf = (a) => String(a?.id || a?.session?.id || "");
  const headerOf = (a) => (a && a.session && a.session.header) || null;
  const isSubagentHeader = (a) => {
    const h = headerOf(a);
    if (!h) return false;
    return Number(h.delegationDepth || 0) > 0 ||
      String(h.origin || "") === "subagent" ||
      String(h.parentSession || "") !== "";
  };
  const targetSessionId = sessionIdOf(agent);
  if (!isSubagentHeader(agent)) {
    return {
      targetSessionId,
      wasSubagent: false,
      appliedToRoot: false,
      rootAgent: agent,
      rootSessionId: targetSessionId,
      subagentChain: [],
    };
  }
  // 沿 parentSession 谱系向上，尽可能解析到最高存活的祖先。
  const chain = [];
  let current = agent;
  const seen = new Set();
  while (current) {
    const sid = sessionIdOf(current);
    if (!sid || seen.has(sid)) break;
    seen.add(sid);
    chain.push(sid);
    const h = headerOf(current);
    const parent = h && h.parentSession ? String(h.parentSession) : "";
    if (!parent) break;
    const parentAgent = (typeof sessionLookup === "function") ? sessionLookup(parent) : null;
    if (!parentAgent || parentAgent === current) break;
    current = parentAgent;
  }
  const appliedToRoot = chain.length > 1 && current !== agent;
  const rootAgent = appliedToRoot ? current : agent;
  return {
    targetSessionId,
    wasSubagent: true,
    appliedToRoot,
    rootAgent,
    rootSessionId: sessionIdOf(rootAgent),
    subagentChain: chain,
  };
}

async function waitAgentIdle(agent, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = controlAgentState(agent);
    if (state === "idle" || state === "cancelled" || state === "stopped") return true;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return false;
}

function currentModelSelection(ctx, request) {
  const provider = String(request.provider || "");
  const model = String(request.model || "");
  if (provider && model) return { provider, model };
  const selection = typeof ctx.agentDefaultModel?.currentSelection === "function"
    ? ctx.agentDefaultModel.currentSelection() : null;
  return {
    provider: provider || String(selection?.provider || ""),
    model: model || String(selection?.model || ""),
  };
}

async function runBridgeDiagnosis(ctx, request, signal) {
  if (typeof ctx.llm?.stream !== "function") throw new Error("llm-unavailable");
  const selection = currentModelSelection(ctx, request);
  if (!selection.provider || !selection.model) throw new Error("judge-model-unavailable");
  const context = String(request.context || "").slice(0, CONTROL_MAX_CONTEXT);
  const goal = String(request.goal || "").slice(0, 2000);
  const prompt = [
    "你是执行中的 Agent 的独立规划诊断器。不要调用工具，不要泛泛解释。",
    "根据当前用户目标和最近一个步骤批次，输出一份可以直接交给 Agent 执行的下一步计划。",
    "计划必须包含：当前目标、最强假设、支持证据、反对证据、下一项最小可证伪实验；",
    "完成前避免继续无目的 Search/Read。只输出计划正文，不要输出 JSON、前言或道歉。",
    `当前用户目标：${goal || "（未知）"}`,
    `最近上下文：\n${context || "（无）"}`,
  ].join("\n\n");
  const messages = [createUserMessage({
    content: [{ type: "text", text: prompt }],
    source: { kind: "plugin", plugin: PLUGIN_ID },
  })];
  let output = "";
  let reasoning = "";
  for await (const chunk of ctx.llm.stream({
    provider: selection.provider,
    model: selection.model,
    messages,
    // 推理型模型会先消耗 token 在 reasoning 上，700 经常被吃光导致正文为空
    // （实机复现：empty-diagnosis）。给足预算，正文才出得来。
    maxTokens: 2048,
    purpose: "dsh-pet-watchdog-replan",
    signal,
  })) {
    if (chunk?.type === "text-delta") output += String(chunk.text || "");
    else if (chunk?.type === "reasoning-delta" || chunk?.type === "reasoning") reasoning += String(chunk.text || "");
  }
  output = output.trim();
  if (!output) {
    console.warn(`[${PLUGIN_ID}] diagnosis empty (reasoning ${reasoning.length} chars, model ${selection.provider}/${selection.model})`);
    throw new Error("empty-diagnosis");
  }
  return output.slice(0, CONTROL_MAX_CONTEXT);
}

async function handleControlRequest(ctx, request) {
  const id = String(request?.id || "");
  const operation = String(request?.operation || "");
  const sessionId = String(request?.sessionId || "");
  if (!id || !sessionId || !["interrupt", "replan"].includes(operation)) {
    return { ok: false, operation, sessionId, phase: "invalid", error: "invalid-control-request", foundAgent: false, cancelInvoked: false };
  }
  if (Date.now() - Number(request.ts || 0) > CONTROL_MAX_REQUEST_AGE_MS) {
    return { ok: false, operation, sessionId, phase: "stale", error: "stale-control-request", foundAgent: false, cancelInvoked: false };
  }
  const agent = controlAgent(ctx, sessionId);
  writeRecord({ event: "bridge/control-received", requestId: id, sessionId,
    operation, foundAgent: !!agent && agentBelongsToLiveSession(agent, sessionId), agentState: controlAgentState(agent) });
  const foundAgent = !!agent && agentBelongsToLiveSession(agent, sessionId);
  if (!foundAgent) {
    if (operation === "interrupt" && knownSessions.has(sessionId)) {
      return { ok: true, operation, sessionId, phase: "already-idle", alreadyIdle: true, foundAgent: false, cancelInvoked: false };
    }
    return { ok: false, operation, sessionId, phase: "not-found", error: "session-not-found", foundAgent: false, cancelInvoked: false };
  }
  // 把控制归一到根会话：子代理 → 其最高存活祖先；顶层 session → 自身。
  // 这样 interrupt 停根 agent 的当前回合（主 agent 不会再补派新子代理），
  // replan 给根 agent 注入重规划建议，而不是只作用于空转的子代理。
  const resolved = resolveControlRoot(agent, (sid) => liveAgents.get(String(sid)));
  const controlTarget = resolved.appliedToRoot ? resolved.rootAgent : agent;
  const rootNorm = {
    wasSubagent: resolved.wasSubagent,
    appliedToRoot: resolved.appliedToRoot,
    rootSessionId: resolved.rootSessionId,
    subagentChain: resolved.subagentChain,
  };
  let cancelInvoked = false;
  try {
    if (operation === "interrupt") {
      // Terminate means terminate: discard pending watchdog/user steering too.
      await controlTarget.cancel("dsh-pet-watchdog", { keepInbox: false });
      cancelInvoked = true;
      // 用户点的是这个子代理：主 agent 的回合取消未必级联到已发布的子代理
      // 自身 driver，显式再停一次目标，确保用户看到的那个空转子代理确实停下。
      if (resolved.appliedToRoot && agent !== controlTarget) {
        try { agent.cancel("dsh-pet-watchdog", { keepInbox: false }); } catch {}
      }
      if (await waitAgentIdle(controlTarget)) {
        return { ok: true, operation, sessionId, phase: "cancelled", alreadyIdle: false, foundAgent: true, cancelInvoked, ...rootNorm };
      }
      return { ok: false, operation, sessionId, phase: "timeout", error: "cancel-timeout", foundAgent: true, cancelInvoked, ...rootNorm };
    }
    // Stop the active driver first.  keepInbox is essential: it prevents a
    // watchdog request from deleting ordinary queued Agent input.
    await controlTarget.cancel("dsh-pet-watchdog-replan", { keepInbox: true });
    cancelInvoked = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.max(1000, Number(request.timeoutMs || 8000)));
    let plan;
    try {
      plan = await runBridgeDiagnosis(ctx, request, controller.signal);
    } finally {
      clearTimeout(timeout);
    }
    await controlTarget.steer(createUserMessage({
      content: [{ type: "text", text: plan }],
      source: { kind: "plugin", plugin: PLUGIN_ID },
    }));
    return { ok: true, operation, sessionId, phase: "replanned", plan, foundAgent: true, cancelInvoked, ...rootNorm };
  } catch (err) {
    console.warn(`[${PLUGIN_ID}] control failed: ${String(err?.message || err)}`);
    return { ok: false, operation, sessionId, phase: "failed", error: "bridge-internal-error", foundAgent: true, cancelInvoked, ...rootNorm };
  }
}

function writeControlOutcome(id, request, result) {
  const controlResult = { source: "bridge", requestId: id, operation: request.operation,
    sessionId: request.sessionId, ok: !!result.ok, phase: result.phase || "",
    error: result.error || "", alreadyIdle: !!result.alreadyIdle,
    foundAgent: !!result.foundAgent, cancelInvoked: !!result.cancelInvoked,
    wasSubagent: !!result.wasSubagent, appliedToRoot: !!result.appliedToRoot,
    rootSessionId: String(result.rootSessionId || "") };
  writeControlResponse(id, result);
  writeRecord({ event: "bridge/control-result", ...controlResult });
  writeRecord({ event: "watchdog/control-result", ...controlResult });
}

function startControlQueue(ctx) {
  // 幂等：DSH 可能在进程内多次调用插件 apply（实测每 ~2s 一次，见本文件头
  // 说明），每次重入若重建 interval 会叠出多路 watchdog 轮询（双读/竞态）。
  // 已在跑则复用现有调度，不重复建。
  if (controlQueueStarted) return controlQueueTimer;
  controlQueueStarted = true;
  let busy = false;
  const timer = setInterval(async () => {
    if (busy) return;
    let names = [];
    try {
      names = fs.readdirSync(bridgeDir()).filter(name => name.startsWith("watchdog-request-") && name.endsWith(".json"));
    } catch { return; }
    for (const name of names) {
      const id = name.slice("watchdog-request-".length, -".json".length);
      const source = path.join(bridgeDir(), name);
      const claimed = path.join(bridgeDir(), `watchdog-processing-${id}.json`);
      try { fs.renameSync(source, claimed); } catch { continue; }
      busy = true;
      try {
        let request;
        try { request = JSON.parse(fs.readFileSync(claimed, "utf8")); }
        catch { request = { id, operation: "", sessionId: "" }; }
        const result = await handleControlRequest(ctx, request);
        writeControlOutcome(id, request, result);
      } catch (err) {
        console.warn(`[${PLUGIN_ID}] control internal error: ${String(err?.message || err)}`);
        writeControlOutcome(id, request || { operation: "", sessionId: "" }, {
          ok: false, phase: "failed", error: "bridge-internal-error", foundAgent: false, cancelInvoked: false,
        });
      } finally {
        try { fs.rmSync(claimed, { force: true }); } catch {}
        busy = false;
      }
      break;
    }
  }, CONTROL_POLL_MS);
  if (timer.unref) timer.unref();
  ctx.effect?.(() => () => clearInterval(timer), `${PLUGIN_ID}.control-queue()`);
  registerDisposer(() => clearInterval(timer));
  controlQueueTimer = timer;
  return timer;
}

// 过程汇报：工具调用事件（state 不变，只带 tool 字段，桌宠端据此弹「正在跑命令…」）
// 注：工具名在 assistant/message 的 tool-call 块与独立 tool/call 事件中均可获得，
// 统一按 callId 去重写入（见下方 session/event 处理），不再单独 writeTool。

// ===== 卡住检测数据增强 =====
// 桌宠端 stuck_detector 需要只读的最终结果观察点。这里在转发事件时附带
// 轻量字段（工具名、参数指纹、成败、错误码/文本、耗时），不改变任何 DSH 流程。
const ARGS_KEY_LENGTH = 64;
const TEXT_MAX = 300;

// ===== 硬失败判定（execution/failed）=====
// 规则：只有 DSH 以「本轮出错的 turn 结尾」为真才可能提醒，正常完成绝不误报。
//   DSH 的 turn/end 自带 data.reason.kind：completed / error / aborted /
//   blocked / max-tokens。completed（正常收尾）等非 error 结尾一律不判失败——
//   即使中途出现过模型重试或工具失败、之后又恢复并正常跑完。
//   真·重试耗尽 = 连续 llm/retry 后 DSH 抛错 → reason.kind === "error"。
//   工具最终失败同理：只有 turn 以 error 结尾且本 turn 有工具失败无成功才判。
// 重试计数只在 turn 内生效，且「恢复即清零」：出现模型成功产出（assistant/
// message、tool/call、成功的 tool/result）就把 retries 归零——只统计距离上次
// 恢复后的连续重试，绝不把不同时段已恢复的抖动累加成长期故障。
// 只在 turn/end 时判定并写一条脱敏记录（错误码保留、错误正文不落盘）。
const RETRY_EXHAUSTED_THRESHOLD = 4;
// 限流/连接超时类重试只在同一 session 连续达到 5 次时提醒一次（识别口径与
// isModelAccessError 一致：429/RATE_LIMIT 与 TIMEOUT/连接断类故障都算）。
// 原始 llm/retry 仍然逐条转发，便于桌宠侧做详细诊断；这里只抑制高优先级
// model_access 事件，避免一次短暂抖动连续轰炸桌宠。
const RETRY_EVENT_THRESHOLD = 5;

// 每个 turn 的状态：sessionKey -> {retries, hadSuccess, hadFailure,
//   lastErrorCode, lastErrorMessage, lastRetryCode, turnActive}
// retries = 距上次恢复后的连续模型重试次数（恢复即清零，见上方规则）。
const turnStatsMap = new Map();

// sessionKey -> { count, notified }
// 只统计连续的 llm/retry 限流事件。任意其他 session 事件、连接成功或 Agent
// 状态变化都会清零，因此不会把不同阶段的重试拼成一次长期故障。
const retryConnectionStats = new Map();

function resetRetryConnection(sessionKey) {
  retryConnectionStats.delete(String(sessionKey || "session:unknown"));
}

function noteRetryConnection(sessionKey) {
  const key = String(sessionKey || "session:unknown");
  const current = retryConnectionStats.get(key) || { count: 0, notified: false };
  current.count += 1;
  retryConnectionStats.set(key, current);
  if (current.count !== RETRY_EVENT_THRESHOLD || current.notified) return false;
  current.notified = true;
  return true;
}

// 用于硬失败判定的 session 键：优先 session.id，回退到 event.data.turn
function sessionKeyOf(_session, event) {
  if (_session && _session.id) return String(_session.id);
  const data = (event && event.data) || {};
  if (data && data.turn) return "turn:" + String(data.turn);
  return "session:unknown";
}

function _turnStats(sessionKey) {
  if (!turnStatsMap.has(sessionKey)) {
    turnStatsMap.set(sessionKey, {
      retries: 0, hadSuccess: false, hadFailure: false,
      lastErrorCode: "", lastErrorMessage: "", lastRetryCode: "", turnActive: false,
    });
  }
  return turnStatsMap.get(sessionKey);
}

function _endTurnStats(sessionKey) {
  turnStatsMap.delete(sessionKey);
}

// turn 开始/异常兜底：把单个 turn 统计重置为全新状态（绝不跨 turn 累计）。
function resetTurnStats(st) {
  st.retries = 0;
  st.hadSuccess = false;
  st.hadFailure = false;
  st.lastErrorCode = "";
  st.lastErrorMessage = "";
  st.lastRetryCode = "";
  st.turnActive = true;
  return st;
}

// 记录一次模型重试：只累加连续计数（恢复信号会把 retries 归零），并记住
// 最后一次重试的错误码（重试耗尽时 execution/failed 用它标注根因）。
function noteStatsRetry(st, errorCode) {
  st.retries += 1;
  if (errorCode) st.lastRetryCode = String(errorCode).slice(0, 48);
}

// 「恢复即清零」：模型成功产出/流程继续推进 → 连续重试计数归零。绝不把已经
// 恢复的抖动计入「重试耗尽」（否则正常完成的 turn 会被误判成硬失败）。
function noteStatsRecovery(st) {
  st.retries = 0;
}

// 记录一次工具结果对硬失败判定的影响（turn/start 重置，tool/result 累计）
function noteStatsToolResult(st, ok, errorCode, errorMessage) {
  st.turnActive = true;
  if (ok) {
    st.hadSuccess = true;
    // 工具执行成功说明模型调用链已恢复推进——同一 turn 内此前任何模型
    // 重试都不再计入「耗尽」判定（与恢复信号同语义）。
    st.retries = 0;
  } else {
    st.hadFailure = true;
    if (errorCode) st.lastErrorCode = String(errorCode).slice(0, 48);
    if (errorMessage) st.lastErrorMessage = truncate(errorMessage);
  }
}

// 按 sessionKey 包装（生产事件路径使用）：
function noteTurnRetry(sessionKey, errorCode) {
  noteStatsRetry(_turnStats(sessionKey), errorCode);
}

function noteTurnRecovery(sessionKey) {
  noteStatsRecovery(_turnStats(sessionKey));
}

function noteTurnToolResult(sessionKey, ok, errorCode, errorMessage) {
  noteStatsToolResult(_turnStats(sessionKey), ok, errorCode, errorMessage);
}

// turn/end 时的硬失败判定（纯函数，供 Node 回归测试直接驱动）：
// 只认 DSH 的 reason.kind === "error"（本轮真的出错终止）；completed /
// aborted / blocked / max-tokens / reason 缺失 → 一律不写 execution/failed。
// 返回要写盘的对象（含脱敏错误码），或 null（不提醒）。
function decideTurnEndFailure(reason, st) {
  if (!st || !st.turnActive) return null;
  const kind = reason && reason.kind ? String(reason.kind) : "";
  if (kind !== "error") return null;
  const retryExhausted = st.retries >= RETRY_EXHAUSTED_THRESHOLD;
  const toolFailed = st.hadFailure && !st.hadSuccess;
  if (!retryExhausted && !toolFailed) return null;
  const reasonCode = reason && reason.error && reason.error.code
    ? String(reason.error.code) : "";
  // 错误码按失败来源选取（只落码不落错误正文）：
  //   模型重试耗尽 → 最近一次 llm/retry 错误码，缺省回退 turn/end 终止错误码
  //   工具最终失败 → 工具错误码，缺省回退终止错误码（generic 码没有工具码信息量大）
  const errorCode = retryExhausted
    ? String(st.lastRetryCode || reasonCode || st.lastErrorCode || "")
    : String(st.lastErrorCode || reasonCode || "");
  return {
    event: "execution/failed",
    // failureType 与活动/过程事件（tool/call 的 tool）解耦：模型重试耗尽 =
    // 模型请求链连续重试后仍失败；tool_failed = 工具调用最终失败。不再是
    // 语义含糊的 "tool"/"model_request"，也不会与协议保留字段 source（Agent
    // 来源）撞名。
    failureType: retryExhausted ? "model_retry_exhausted" : "tool_failed",
    retryExhausted: !!retryExhausted,
    retries: st.retries,
    errorCode: errorCode.slice(0, 48),
    errorMessage: String(st.lastErrorMessage || ""),
  };
}

function summarizeArgs(args) {
  if (args === undefined || args === null) return "";
  let obj = args;
  if (typeof obj === "string") {
    try { obj = JSON.parse(obj); } catch { return String(obj).slice(0, ARGS_KEY_LENGTH); }
  }
  if (typeof obj !== "object" || Array.isArray(obj)) {
    return JSON.stringify(obj).slice(0, ARGS_KEY_LENGTH);
  }
  const keys = Object.keys(obj).sort();
  const parts = keys.map(k => String(k));
  // 命令型工具：加入 argv[0]（如 pip/curl/npm）使「同命令换参数」聚成同一指纹
  const cmdKeys = ["command", "cmd", "shell", "script", "argv", "exec"];
  for (const k of cmdKeys) {
    const v = obj[k];
    if (v !== undefined && v !== null) {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      const argv0 = s.trim().split(/\s+/)[0];
      if (argv0) parts.push("argv0:" + argv0.slice(0, 48));
      break;
    }
  }
  return parts.slice(0, 16).join(",").slice(0, ARGS_KEY_LENGTH);
}

// Keep the command that is actually executed separate from user-facing tool
// descriptions.  The watchdog compares execution semantics; labels such as
// "Read file 1st time" must not make identical commands look different.
function commandFromArgs(args) {
  if (args === undefined || args === null) return "";
  let obj = args;
  if (typeof obj === "string") {
    try { obj = JSON.parse(obj); } catch { return ""; }
  }
  if (!obj || typeof obj !== "object") return "";
  for (const key of ["command", "cmd", "shell", "script", "exec", "argv"]) {
    const value = obj[key];
    if (value === undefined || value === null) continue;
    return truncate(typeof value === "string" ? value : JSON.stringify(value), 800);
  }
  return "";
}

function truncate(s, max = TEXT_MAX) {
  if (typeof s !== "string") s = String(s || "");
  return s.length > max ? s.slice(0, max) : s;
}

function messageText(data) {
  const d = data || {};
  for (const value of [d.text, d.prompt, d.message && d.message.text]) {
    if (typeof value === "string" && value.trim()) return truncate(value.trim(), 1200);
  }
  const content = (d.message && d.message.content) || d.content;
  if (typeof content === "string") return truncate(content.trim(), 1200);
  if (!Array.isArray(content)) return "";
  return truncate(content.map(block => {
    if (typeof block === "string") return block;
    return block && typeof block.text === "string" ? block.text : "";
  }).filter(Boolean).join(" ").replace(/\s+/g, " ").trim(), 1200);
}

function agentLabelFor(sessionId) {
  const agent = liveAgents.get(String(sessionId));
  if (!agent) return "DSH";
  return String(agent.name || agent.displayName || agent.label || agent.id || "DSH");
}

function basenameOf(value) {
  const raw = String(value || "").trim().replace(/[\\/]+$/, "");
  if (!raw) return "";
  return raw.split(/[\\/]/).pop() || "";
}

function sessionIdOfAgent(agent, session) {
  return String(agent?.session?.id || agent?.id || session?.id || "");
}

function projectionTitle(session) {
  const values = session?.projections?.values;
  const title = values && typeof values === "object" ? values.title : "";
  return typeof title === "string" && title.trim() ? title.trim() : "";
}

function extractSessionMeta(agent, session, summary = null, workspace = null) {
  if (!agent && !session && !summary) return null;
  const sessionId = String(summary?.sessionId || sessionIdOfAgent(agent, session));
  if (!sessionId) return null;

  // 运行时 Session 没有 UI 名称；真实标题来自 session.list 的 summary.projections.title。
  const sessionName = String(
    summary?.projections?.values?.title || projectionTitle(session) ||
    session?.title || session?.label || session?.name || "",
  ).trim();
  // 真实项目名来自 workspace.list 的 title；cwd 只作最后的真实 basename 降级。
  const projectName = String(
    workspace?.title || basenameOf(workspace?.path) || basenameOf(summary?.cwd) ||
    basenameOf(session?.cwd) || "",
  ).trim();
  const agentName = String(agent?.name || agent?.displayName || "DSH").trim() || "DSH";

  const parts = ["DSH"];
  if (projectName) parts.push(projectName);
  if (sessionName) parts.push(sessionName);
  const displayLabel = parts.join(" · ");
  return { sessionId, sessionName, projectName, agentName, displayLabel };
}

// apiProxy 缺失（当前 dsh 发布版无此服务）时的真实标题兜底：
// dsh 把会话标题/工作目录缓存在 ~/.dsh/storages/session_projcache/sessions/<sid>.json。
function readProjcacheSummary(sessionId) {
  const sid = String(sessionId || "");
  if (!sid) return null;
  const candidates = sid.startsWith("session-") ? [sid] : [sid, `session-${sid}`];
  for (const name of candidates) {
    try {
      const file = path.join(os.homedir(), ".dsh", "storages", "session_projcache", "sessions", `${name}.json`);
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const title = data?.record?.rows?.title?.val;
      const cwd = data?.record?.identity?.cwd;
      if (!title && !cwd) continue;
      return { sessionId: name, cwd: cwd || "", projections: { values: { title: title || "" } } };
    } catch { /* 缓存不存在或损坏：跳过 */ }
  }
  return null;
}

async function refreshSessionMetadata(ctx) {
  if (metadataRefreshPromise) return metadataRefreshPromise;
  metadataRefreshPromise = (async () => {
    try {
      // apiProxy 不进 inject（当前 dsh 发布版无此服务，强依赖会让插件无法激活）；
      // 用 ctx.get 免 inject 读取，缺失时返回 undefined。
      const api = typeof ctx?.get === "function" ? ctx.get("apiProxy", false) : undefined;
      if (!api?.sessions?.list || !api?.workspace?.list) {
        // 兜底：读 dsh 本地会话缓存投影出 summary，复用同一条写 meta 通路。
        for (const [id, agent] of liveAgents) {
          const sid = String(agent?.session?.id || id);
          writeSessionMeta(agent, agent?.session, readProjcacheSummary(sid), null);
        }
        return;
      }
      const request = () => ({ rpcId: randomUUID(), payload: {} });
      const [sessionsResponse, workspacesResponse] = await Promise.all([
        api.sessions.list(request()),
        api.workspace.list(request()),
      ]);
      const sessionItems = sessionsResponse?.result?.ok
        ? sessionsResponse.result.value?.items || [] : sessionsResponse?.items || [];
      const workspaceItems = workspacesResponse?.result?.ok
        ? workspacesResponse.result.value?.items || [] : workspacesResponse?.items || [];
      const summaries = new Map(sessionItems.map(item => [String(item.sessionId || ""), item]));
      const workspaces = new Map();
      for (const workspace of workspaceItems) {
        for (const id of workspace.sessionIds || []) workspaces.set(String(id), workspace);
      }
      for (const [id, agent] of liveAgents) {
        const summary = summaries.get(String(agent?.session?.id || id));
        const session = agent?.session;
        const workspace = workspaces.get(String(summary?.sessionId || id));
        writeSessionMeta(agent, session, summary, workspace);
      }
    } catch (error) {
      console.warn(`[${PLUGIN_ID}] 获取 session/workspace 元数据失败: ${String(error?.message || error)}`);
    } finally {
      metadataRefreshPromise = null;
    }
  })();
  return metadataRefreshPromise;
}

function scheduleSessionMetadataRefresh(ctx) {
  if (metadataRefreshTimer) clearTimeout(metadataRefreshTimer);
  metadataRefreshTimer = setTimeout(() => {
    metadataRefreshTimer = null;
    refreshSessionMetadata(ctx);
  }, 50);
  if (metadataRefreshTimer.unref) metadataRefreshTimer.unref();
}

function writeSessionMeta(agent, session, summary = null, workspace = null) {
  const meta = extractSessionMeta(agent, session, summary, workspace);
  if (!meta) return;

  const sid = meta.sessionId;
  // 去重：仅当 sessionName 或 projectName 变化时才重发
  const cached = sessionMetaCache.get(sid);
  if (cached && cached.displayLabel === meta.displayLabel) return;

  sessionMetaCache.set(sid, meta);
  writeRecord({
    type: "session/meta",
    sessionId: sid,
    projectName: meta.projectName || "",
    sessionName: meta.sessionName || "",
    agentName: meta.agentName || "DSH",
  });

  // 临时诊断：仅第一条 session 输出字段结构，用于确认真实 DSH payload
  if (sessionMetaCache.size <= 1) {
    writeRecord({
      type: "debug/session-shape",
      sessionId: sid,
      rawLabel: session?.label ?? null,
      rawTitle: session?.title ?? null,
      rawName: session?.name ?? null,
      rawProject: session?.parent?.name ?? session?.project?.name ?? null,
      rawWorkspace: session?.workspace?.path ?? null,
      rawAgentName: agent?.name ?? null,
    });
  }
}

const lastEvidenceByCallTarget = new Map();

function toolResultInfo(data) {
  const message = (data && data.message) || {};
  const callId = message.callId || (message.source && message.source.callId) || "";
  let isError = false, errorText = "", errorCode = "", resultText = "";
  const content = message.content;
  if (Array.isArray(content)) {
    for (const block of content) {
      if (!block || typeof block !== "object") continue;
      if (block.type === "tool-result") {
        if (block.isError) isError = true;
        const c = block.content;
        if (c !== undefined && c !== null) {
          resultText = typeof c === "string" ? c : JSON.stringify(c);
          if (block.isError) errorText = resultText;
        }
        break;
      }
    }
  }
  const err = data && data.error;
  if (err && typeof err === "object") {
    if (!errorCode) errorCode = String(err.code || err.name || err.type || "");
    if (!errorText) errorText = typeof err.message === "string" ? err.message : "";
  }
  return {
    callId: String(callId), isError, errorText: truncate(errorText),
    resultText: truncate(resultText, 240), errorCode: errorCode.slice(0, 48),
  };
}

const pendingTools = new Map(); // callId -> {tool, argsKey, command, sessionId, t0}
const writtenToolCallIds = new Set(); // callId -> 已写入过 tool/call 记录（去重）

function noteToolCall(callId, tool, args, sessionId = "") {
  if (!callId || pendingTools.has(callId)) return;
  pendingTools.set(callId, {
    tool: String(tool || ""), argsKey: summarizeArgs(args),
    command: commandFromArgs(args), sessionId: String(sessionId || ""), t0: Date.now(),
  });
  if (pendingTools.size > 512) { // 防无限增长
    const now = Date.now();
    for (const [k, v] of pendingTools) {
      if (now - v.t0 > 30 * 60 * 1000) pendingTools.delete(k);
    }
  }
}

function consumeToolCall(callId) {
  if (!callId) return null;
  const info = pendingTools.get(callId) || null;
  pendingTools.delete(callId);
  return info;
}

// 审批命令只允许从同 session、同工具、且时间窗口内的最近 tool/call 回填。
// 不再使用进程级 lastToolCall，避免并发 session 把普通命令串到审批上。
const recentToolCalls = new Map(); // sessionId -> [{tool, args, callId, ts}]
const APPROVAL_COMMAND_MAX_AGE_MS = 2000;

function noteLatestToolCall(tool, args, sessionId = "", callId = "") {
  const key = String(sessionId || "session:unknown");
  const list = recentToolCalls.get(key) || [];
  list.push({ tool: String(tool || ""), args, callId: String(callId || ""), ts: Date.now() });
  while (list.length > 16) list.shift();
  recentToolCalls.set(key, list);
}

function latestCommandFor(toolName, args, sessionId = "", callId = "") {
  const direct = extractCommand(args);
  if (direct) return direct;
  const key = String(sessionId || "session:unknown");
  const list = recentToolCalls.get(key) || [];
  const expectedTool = String(toolName || "").trim();
  const expectedCall = String(callId || "").trim();
  const now = Date.now();
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const item = list[i];
    if (now - item.ts > APPROVAL_COMMAND_MAX_AGE_MS) continue;
    if (expectedCall && item.callId && item.callId === expectedCall) {
      return extractCommand(item.args);
    }
    if (expectedTool && item.tool === expectedTool) {
      return extractCommand(item.args);
    }
  }
  return "";
}

// ===== v1 DSH state linkage =====
// Forward real DSH session/event types as "simple events" so the pet side
// (pet/dsh_state.py) can collapse them into thinking/working/
// waiting_approval/success/error. Records carry only an event field and no
// state field, so the legacy AgentStatus working/idle baseline is untouched
// and the legacy DshMonitor (which ignores unknown event types) keeps working.
// NOTE: assistant/message, tool/call, tool/result, and llm/retry are handled
// explicitly with enriched data and are NOT in this set to avoid double writes.
const STATE_EVENT_TYPES = new Set([
  "turn/start",
  "turn/end",
  // NOTE: assistant/chunk (streaming) is intentionally NOT forwarded here.
  // It fires many times per second while streaming, and each forwarded event
  // used to trigger a synchronous file write on DSH's main thread, which
  // visibly stuttered DSH and the pet. "thinking" is already covered by
  // user/message and turn/start, so dropping chunk loses no state.
  "step/start",
  "step/end",
  "command/run",
  "command/done",
  "tool-workflow/run-start",
  "tool-workflow/run-end",
  "approval/asked",
  "approval/decided",
]);

// Extract step identifier from a DSH session/event for behavior pattern detection.
// DSH events carry { turn, step, ... } in event.data; the behavior detector on the
// pet side uses step to deduplicate parallel tool calls (same step → one decision).
function stepOf(event) {
  const data = (event && event.data) || {};
  if (data && data.step !== undefined && data.step !== null) return data.step;
  if (data && data.turn !== undefined && data.turn !== null) return `turn:${data.turn}`;
  return null;
}

function sessionIdOf(session, event) {
  if (session && session.id) return String(session.id);
  const data = (event && event.data) || {};
  if (data && data.sessionId) return String(data.sessionId);
  if (data && data.session_id) return String(data.session_id);
  if (data && data.turn !== undefined && data.turn !== null) return `turn:${data.turn}`;
  return "session:unknown";
}

function writeStateEvent(type, step, sessionId, agentName = "") {
  const extra = { event: type };
  if (step !== undefined && step !== null) extra.step = step;
  if (sessionId) extra.sessionId = sessionId;
  if (agentName) extra.agentName = agentName;
  writeRecord(extra);
}

// These events are useful to the exploration watchdog but are not state
// transitions.  Streaming delta events are intentionally excluded: they are
// summarized by their corresponding begin/end event and must not become fake
// Agent decisions.
const WATCHDOG_EVENT_TYPES = new Set([
  "agent_reasoning", "agent_reasoning_raw_content", "web_search_begin", "web_search_end",
  "exec_command_begin", "exec_command_end", "mcp_tool_call_begin", "mcp_tool_call_end",
  "context_compacted", "thread_rolled_back", "task_started", "task_complete",
  "user_action",
]);

// This is the single producer-side source of truth for the event contract.
// Keep AgentStatus: writeRecord supplies it for records that carry no explicit
// event (for example the legacy aggregate state and session metadata records).
// The two dynamic producer sets above are expanded here so the inventory is a
// plain JSON-compatible value for the hello record and for contract tests.

// 审批 UI 请求只由权威 mux 帧（approval/requested，带 rpcId+sessionId）产生
// （见下方 mux 中继）。这里不再提供 writeApprovalRequest：approval/asked 等
// session/event 只是状态/审计信号，绝不能升级成桌宠的审批弹窗——普通工具调用
// （如 pwsh 跑 Get-Location）一旦被宿主标成 approval/asked 就会误弹无法关闭的
// sticky 审批气泡。

// 从审批 arguments 里提取「将要执行的命令」完整内容：
//   bash/shell/pwsh 等命令型工具 → arguments.command / .cmd / .shell / .script
//   write/edit 等文件工具 → filePath（动作对象，非全文）
// 取不到时返回 ""（调用方回退到仅显示工具名）。
function extractCommand(arguments_) {
  if (!arguments_) return "";
  let args = arguments_;
  if (typeof args === "string") {
    try { args = JSON.parse(args); } catch { return ""; }
  }
  if (!args || typeof args !== "object") return "";
  for (const key of ["command", "cmd", "shell", "script", "argv"]) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v.trim();
    if (Array.isArray(v) && v.length) return v.join(" ").trim();
  }
  // 文件型工具：展示动作对象（filePath/path），帮助判断改的是哪个文件
  for (const key of ["filePath", "path"]) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

// ===== user-question blocking interaction =====
// DSH's ask_user_question tool pauses the agent until the human answers, then
// feeds the answer back as an ordinary tool result (see @deepseek-ai/dsh-tool-ask-user
// and the host-apiproxy question/requested + question/resolved mux frames).
// The bridge detects it from the session/event stream:
//   tool/call { name: "ask_user_question", callId, arguments: { questions } }
//     -> authoritative request signal; write question/requested with the payload
//   tool/result { message.callId } matching the pending call
//     -> resolved; write question/resolved
// Records are consumed by pet/dsh_state.py (waiting_question state) and the
// legacy agent_link bubble path (permanent question popup).
const QUESTION_TOOL = "ask_user_question";
const pendingQuestionCallIds = new Set();
// mux rpcId ↔ callId 按到达顺序 FIFO 配对（C3，合入 main 的 075d443）：
// tool/call 注册把 callId 排进会话队列；mux question/requested 帧出队一个
// 并记住 rpcId→callId，后续 question/resolved 按 rpcId 取回。同会话多问题
// 并发时，每个帧拿到的是自己那份 callId，而不是反复取到最旧的一个。
// 否则帧里缺 callId 时 mux 断线后的兜底关闭失效，桌宠气泡永久挂住。
const pendingQuestionRpcPairs = new Map(); // rpcId → callId（resolved 取回后即删）
const pendingQuestionOrder = new Map();    // sessionId → callId[]（FIFO，待 mux 帧出队）

function questionCallIdentity(callId, sessionId) {
  return `${String(sessionId || "")}|${String(callId || "")}`;
}

function registerQuestionCall(callId, sessionId) {
  const id = String(callId || "");
  if (!id || pendingQuestionCallIds.has(questionCallIdentity(id, sessionId))) return false;
  pendingQuestionCallIds.add(questionCallIdentity(id, sessionId));
  const session = String(sessionId || "");
  const queue = pendingQuestionOrder.get(session) || [];
  queue.push(id);
  pendingQuestionOrder.set(session, queue);
  return true;
}

function forgetQuestionCall(callId, sessionId) {
  const id = String(callId || "");
  pendingQuestionCallIds.delete(questionCallIdentity(id, sessionId));
  const session = String(sessionId || "");
  const queue = pendingQuestionOrder.get(session);
  if (queue) {
    const idx = queue.indexOf(id);
    if (idx >= 0) queue.splice(idx, 1);
    if (!queue.length) pendingQuestionOrder.delete(session);
  }
}

function extractQuestions(arguments_) {
  if (!arguments_) return [];
  let args = arguments_;
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      return [];
    }
  }
  if (!args || typeof args !== "object" || !Array.isArray(args.questions)) return [];
  // DSH's question contract is extensible.  Do not project/flatten it: retain
  // every question and option field (including intent and future fields), while
  // cloning so later DSH mutations cannot alter the JSONL record.
  return typeof structuredClone === "function"
    ? structuredClone(args.questions)
    : JSON.parse(JSON.stringify(args.questions));
}

function writeQuestionRequest(callId, questions, sessionId) {
  if (!registerQuestionCall(callId, sessionId)) return; // 已写过，去重
  // 两个路径（tool/call + mux）都无条件写，由 writeRecordDedup 去重：
  // mux 正常时保留 rpcId 版本（可交互）；mux 不可用/连接失败时兜底写提示
  // （无按钮但至少弹窗出现，不会丢问题）。
  writeRecordDedup({
    event: "question/requested",
    callId: String(callId || ""),
    sessionId: String(sessionId || ""),
    questions,
  });
}

function resolveQuestion(callId, sessionId) {
  const id = String(callId || "");
  if (!id || !pendingQuestionCallIds.has(questionCallIdentity(id, sessionId))) return;
  forgetQuestionCall(callId, sessionId);
  // 收尾记录不 gate mux：重复的 question/resolved 无害（桌宠幂等），
  // 但若 mux 在问题中途才连上、丢了对应的 resolved 帧，这里必须兜底写，
  // 否则桌宠会卡死在 waiting_question。
  writeRecord({
    event: "question/resolved",
    callId: id,
    sessionId: String(sessionId || ""),
  });
}

// mux question 帧只带 rpcId，callId 只有 tool/call 兜底路径才登记（复合键
// sessionId|callId，FIFO 队列见 pendingQuestionOrder）。桌宠端升级重建后靠
// callId 与兜底 question/resolved 配对，帧里缺 callId 时 mux 断线后的兜底
// 关闭就失效，气泡永久挂住——按 FIFO 出队补上并记住 rpcId→callId。
function muxQuestionCallId(payload, rpcId) {
  const fromFrame = String(payload.callId || "");
  if (fromFrame) return fromFrame;
  const rpc = String(rpcId || "");
  const paired = pendingQuestionRpcPairs.get(rpc);
  if (paired) return paired;
  const queue = pendingQuestionOrder.get(String(payload.sessionId || ""));
  const next = queue ? queue.shift() : undefined;
  if (next) {
    if (rpc) pendingQuestionRpcPairs.set(rpc, next);
    return next;
  }
  return "";
}

function muxQuestionCallIdForResolved(payload, rpcId) {
  const fromFrame = String(payload.callId || "");
  if (fromFrame) return fromFrame;
  const rpc = String(rpcId || "");
  const paired = pendingQuestionRpcPairs.get(rpc);
  if (paired) {
    pendingQuestionRpcPairs.delete(rpc); // resolved 是终态，取回即清
    return paired;
  }
  return "";
}

function muxQuestionRequestedRecord(rpcId, payload) {
  return {
    event: "question/requested",
    rpcId,
    sessionId: payload.sessionId,
    questions: payload.questions,
    callId: muxQuestionCallId(payload, rpcId),
  };
}

function muxQuestionResolvedRecord(rpcId, payload) {
  return {
    event: "question/resolved",
    rpcId,
    sessionId: payload.sessionId,
    outcome: payload.outcome,
    callId: muxQuestionCallIdForResolved(payload, rpcId),
  };
}

// ===== interactive mux relay =====
// DSH's /api/events.mux (WebSocket) pushes the SAME interaction frames the web
// UI renders: approval/requested, approval/resolved, question/requested,
// question/resolved — each carrying an rpcId the pet needs to answer back via
// POST /api/respond. The bridge connects as one mux client and relays these
// frames (with rpcId + full payload) into dsh.jsonl so the pet can show a
// CLICKABLE bubble and actually resolve the approval/question in DSH, instead
// of only hinting "go click it in the web UI".
// Node >=22 exposes a global WebSocket client (undici) — zero dependency.
let muxSocket = null;
let muxTimer = null;
let muxReconnectMs = 1000;
let muxPortIndex = 0;
// mux 是否已真正连接（onopen 置真、onclose 置假）。mux 连接后，审批/问题的
// 权威记录由 mux 帧（带 rpcId，可交互）提供；未连接时才用旧路径降级写提示。
let muxConnected = false;
// 本模块实例是否已被 dispose：置真后禁止任何新的连接/重连调度。socket.close()
// 是异步的，close 事件会稍后触发 onclose——若没有此标志，dispose 关掉的旧
// mux 会经 onclose→muxScheduleReconnect 复活并在后台重连、继续写盘
// （热重载/卸载后旧实例双写 dsh.jsonl），这是 P1 实测确认的泄漏路径。
let muxDisposed = false;

// DSH 可能跑在 3080（web 默认）或 38080（端口被占时的避让），也可能由
// DSH_PORT 指定——全部作为候选，逐个尝试，任一连上即可（与桌宠端一致）。
function muxCandidatePorts() {
  const ports = [];
  if (process.env.DSH_PORT) ports.push(Number(process.env.DSH_PORT));
  ports.push(3080, 38080);
  return [...new Set(ports.filter((p) => Number.isInteger(p) && p > 0))];
}

function muxScheduleReconnect() {
  if (muxDisposed) return; // dispose 后不再建立任何新重连定时器
  if (muxTimer) clearTimeout(muxTimer);
  muxTimer = setTimeout(() => {
    muxTimer = null;
    muxConnect();
  }, muxReconnectMs);
  muxReconnectMs = Math.min(muxReconnectMs * 2, 15000);
}

function muxConnect() {
  if (muxDisposed) return; // dispose 后旧实例不得复活连接
  if (typeof WebSocket === "undefined") return; // 旧 Node：无 WS，降级为纯提示
  // 幂等：已有 socket（含连接中）时不再重复 new WebSocket——DSH 进程内多次
  // apply（实测每 ~2s 一次）若每次都重连，approval/question 权威帧会散落到
  // 多路/交替连接上，某帧落在 close→open 窗口即丢失 → 桌宠「审批变事后」。
  if (muxSocket && (muxSocket.readyState === WebSocket.OPEN || muxSocket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const ports = muxCandidatePorts();
  if (ports.length === 0) return;
  if (muxPortIndex >= ports.length) muxPortIndex = 0; // 一轮试完回到起点（配合退避）
  const port = ports[muxPortIndex];
  let ws;
  try {
    ws = new WebSocket(`ws://127.0.0.1:${port}/api/events.mux`);
  } catch {
    muxPortIndex = (muxPortIndex + 1) % ports.length;
    muxScheduleReconnect();
    return;
  }
  muxSocket = ws;
  ws.onopen = () => {
    muxReconnectMs = 1000;
    muxPortIndex = 0; // 连上了，下次重连从首选端口开始
    muxConnected = true;
  };
  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      return;
    }
    if (!msg || msg.type !== "server-request" || !msg.payload) return;
    const p = msg.payload;
    try {
      if (p.type === "approval/requested") {
        // 权威 UI 审批请求：必须带 rpcId + sessionId 才可作为桌宠可交互审批落盘
        // （approvalId 正常也会带，供 resolved 精确配对）。缺身份/空占位帧一律
        // 忽略（只留日志），防止误弹无法关闭的审批气泡。
        const rpcId = String(msg.rpcId || "");
        const sessionId = String(p.sessionId || "");
        if (rpcId && sessionId) {
          writeRecordDedup({ event: "approval/request", rpcId, sessionId, approvalId: String(p.approvalId || ""), toolName: p.toolName, command: latestCommandFor(p.toolName, p.arguments) });
        } else {
          console.warn(`[${PLUGIN_ID}] 忽略缺身份的 mux 审批帧: rpcId=${rpcId ? "yes" : "no"} sessionId=${sessionId ? "yes" : "no"}`);
        }
      } else if (p.type === "approval/resolved") {
        writeRecord({ event: "approval/resolved", rpcId: msg.rpcId, sessionId: p.sessionId, approvalId: p.approvalId, outcome: p.outcome });
        writeInteractionResolved("approval", p.sessionId, { rpcId: msg.rpcId, approvalId: p.approvalId }, p.outcome || "approved");
        // 用户介入信号
        writeRecord({
          event: "user_action",
          action: "approval_resolved",
          outcome: String(p.outcome || ""),
          rpcId: msg.rpcId,
          approvalId: p.approvalId,
          sessionId: p.sessionId,
        });
      } else if (p.type === "question/requested") {
        // 用 FIFO 配对记录：mux 帧缺 callId 时按会话队列出队补上并记住
        // rpcId→callId（合入 main 的 C3 修复），防桌宠气泡因缺 callId 挂住。
        writeRecordDedup(muxQuestionRequestedRecord(msg.rpcId, p));
      } else if (p.type === "question/resolved") {
        const questionRpcId = p.questionRpcId || msg.rpcId;
        writeRecord(muxQuestionResolvedRecord(questionRpcId, p));
        writeInteractionResolved("question", p.sessionId, { rpcId: questionRpcId, callId: muxQuestionCallIdForResolved(p, questionRpcId) }, p.outcome || "answered");
        // 用户介入信号
        writeRecord({
          event: "user_action",
          action: "question_resolved",
          outcome: String(p.outcome || ""),
          rpcId: questionRpcId,
          sessionId: p.sessionId,
        });
      }
    } catch {}
  };
  ws.onclose = () => {
    if (muxDisposed) return; // dispose 触发的 close：不得换端口重连
    if (muxSocket === ws) muxSocket = null;
    muxConnected = false;
    muxPortIndex = (muxPortIndex + 1) % ports.length; // 换下一个候选端口
    muxScheduleReconnect();
  };
  ws.onerror = () => {
    try { ws.close(); } catch {}
  };
}

// ===== 写盘去抖（batch flush） =====
// 之前每条事件都立即 mkdirSync+statSync+appendFileSync 同步写盘；DSH 会话忙碌
// （流式/工具密集）时，这些逐事件同步文件 I/O 会卡住 DSH 的 Node 主线程，连带
// 桌宠感知卡顿。改为：事件先入内存队列，合并到一个延迟 flush 里一次性落盘——
// 一个节流窗口内无论来多少事件，DSH 主线程都只做一次文件写。

// 每个 DSH 实例写自己的文件（dsh-{pid}.jsonl），避免 Windows 多实例并行写
// 同一文件的数据行交织；consumers 读取全部 dsh-*.jsonl。
const INSTANCE_FILE = `dsh-${process.pid}.jsonl`;

const FLUSH_DELAY_MS = 80; // 事件合批窗口：80ms 内的记录合并成一次写盘
let writeQueue = [];
let flushTimer = null;
// 桥目录是否已就绪（按目录路径记忆）：mkdirSync(recursive) 对已存在目录实测
// ~0.46ms/次，而 flush 每 80ms 一次（DSH 活跃时把 0.46ms 主线程占用放大到
// ~0.6%）——目录首次创建后就不再需要 mkdir。记录已就绪的目录路径：APPDATA
// 可能变化（多实例/测试/换用户），目录不同时必须重新 mkdir。
let bridgeDirReadyPath = "";

function flushPending() {
  flushTimer = null;
  if (writeQueue.length === 0) return;
  const batch = writeQueue.splice(0, writeQueue.length).join("");
  try {
    const dir = bridgeDir();
    if (bridgeDirReadyPath !== dir) {
      fs.mkdirSync(dir, { recursive: true });
      bridgeDirReadyPath = dir;
    }
    const file = path.join(dir, INSTANCE_FILE);
    try {
      // 超上限轮转：dsh-{pid}.jsonl → dsh-{pid}.jsonl.1（只留一代）
      if (fs.existsSync(file) && fs.statSync(file).size > MAX_BYTES) {
        // Windows 不允许 rename 覆盖已存在目标，先删再转
        fs.rmSync(file + ".1", { force: true });
        fs.renameSync(file, file + ".1");
      }
    } catch {}
    fs.appendFileSync(file, batch, "utf8");
  } catch {
    // 静默失败：桥接是锦上添花，绝不能影响 DSH 本体。
    // 目录可能被外部删除/换盘，清路径让下轮重新 mkdir 自愈。
    bridgeDirReadyPath = "";
  }
}

function writeRecord(extra) {
  try {
    // 从 sessionMetaCache 补充字面上的 projectName / sessionName。
    // sessionName 不再借用 label，避免下游把工具标签与会话名混淆。
    const sid = extra.sessionId;
    if (sid) {
      const meta = sessionMetaCache.get(sid);
      if (meta) {
        if (meta.projectName) extra.projectName = meta.projectName;
        if (meta.sessionName) extra.sessionName = meta.sessionName;
      }
    }
    writeQueue.push(
      JSON.stringify({
        ts: Date.now() / 1000,
        agent: "dsh",
        event: "AgentStatus",
        ...extra,
        bridgeProtocolVersion: BRIDGE_PROTOCOL_VERSION,
        bridgeVersion: BRIDGE_VERSION,
      }) + "\n",
    );
    if (flushTimer === null) {
      flushTimer = setTimeout(flushPending, FLUSH_DELAY_MS);
      if (flushTimer.unref) flushTimer.unref(); // 不阻止 DSH 进程退出
    }
  } catch {
    // 入队失败也静默：绝不影响 DSH
  }
}

function writeBridgeHello() {
  writeRecord({
    event: "bridge/hello",
    bridgeProtocolVersion: BRIDGE_PROTOCOL_VERSION,
    bridgeVersion: BRIDGE_VERSION,
    capabilities: BRIDGE_CAPABILITIES,
    emittedEvents: BRIDGE_EVENT_INVENTORY,
  });
}

// ===== 审批/问题写盘去重（P0 竞态防线） =====
// approval/request 现在只由权威 mux 帧（approval/requested）产生，但 mux 重连/
// 重复投递仍可能对同一条审批多次触发；question/requested 仍走 tool/call + mux
// 双通道。这里按可得的稳定身份去重：短窗口内同一条审批/问题只落一条记录，
// 杜绝「先弹无按钮气泡、交互版被队列压住」的重复气泡竞态。优先保留带 rpcId
// 的可交互版本。
const INTERACTION_DEDUP_MS = 8000;
const interactionSeen = new Map(); // key -> { ts, hasRpcId }
const resolvedInteractionIds = new Set();

function interactionIdentity(kind, sessionId, values = {}) {
  const id = String(values.requestId || values.rpcId || values.approvalId || values.callId || "");
  return id && `${String(sessionId || "")}|${kind}|${id}`;
}

function writeInteractionResolved(kind, sessionId, values = {}, outcome = "") {
  const identity = interactionIdentity(kind, sessionId, values);
  if (!identity || resolvedInteractionIds.has(identity)) return false;
  resolvedInteractionIds.add(identity);
  if (resolvedInteractionIds.size > 2048) resolvedInteractionIds.delete(resolvedInteractionIds.values().next().value);
  writeRecord({
    event: "interaction/resolved", source: "dsh", agentName: agentLabelFor(sessionId),
    sessionId: String(sessionId || ""), kind,
    requestId: String(values.requestId || ""), rpcId: String(values.rpcId || ""),
    approvalId: String(values.approvalId || ""), callId: String(values.callId || ""),
    outcome: String(outcome || ""),
  });
  return true;
}

function _interactionDedupKeys(extra) {
  const ev = extra.event || "";
  const keys = [];
  if (ev === "approval/request" || ev === "approval/resolved") {
    if (extra.approvalId) keys.push(`ap:${extra.approvalId}`);
    else if (extra.rpcId) keys.push(`ap:${extra.rpcId}`);
    // sessionId 只能与 approvalId/rpcId 组合使用，单用 sessionId 会误从不同审批
    // 的同 session 事件上去重（如两个不同审批在同一 session 中先后到达）。
    if (extra.approvalId && extra.sessionId) keys.push(`ap:se:${extra.sessionId}:${extra.approvalId}`);
    else if (extra.rpcId && extra.sessionId) keys.push(`ap:se:${extra.sessionId}:${extra.rpcId}`);
    // tool+command 降级去重键：仅在没有任何稳定审批身份（approvalId/rpcId）
    // 时才使用——无条件加入会让同一会话内两条身份不同的审批（同命令）在 8s
    // 窗口内互相吞掉（P1-4）。有 sessionId 时拼进键里做基本隔离。
    const tool = extra.toolName || extra.tool || "";
    const cmd = extra.command || "";
    const session = extra.sessionId || "";
    const hasIdentity = Boolean(extra.approvalId || extra.rpcId);
    if (!hasIdentity && (tool || cmd)) {
      keys.push(session ? `ap:tc:${session}:${tool}|${cmd}` : `ap:tc:${tool}|${cmd}`);
    }
  } else if (ev === "question/requested" || ev === "question/resolved") {
    if (extra.rpcId) keys.push(`qu:${extra.rpcId}`);
    // sessionId 同理：与 rpcId 组合
    if (extra.rpcId && extra.sessionId) keys.push(`qu:se:${extra.sessionId}:${extra.rpcId}`);
  }
  return keys;
}

function writeRecordDedup(extra) {
  const keys = _interactionDedupKeys(extra);
  const now = Date.now();
  const hasRpc = !!extra.rpcId;
  if (keys.length) {
    let blocked = false;
    for (const k of keys) {
      const prev = interactionSeen.get(k);
      if (prev !== undefined && now - prev.ts < INTERACTION_DEDUP_MS) {
        // 已有同身份记录：新版本无 rpcId 且旧版本有 → 丢弃本版（不降级）
        // 新版本有 rpcId 且旧版本无 → 允许补写（升级为可交互），消费端会合并
        if (!hasRpc && prev.hasRpcId) { blocked = true; break; }
        if (hasRpc && !prev.hasRpcId) { continue; } // 允许升级写盘
        blocked = true; break; // 完全相同或都有 rpcId：重复丢弃
      }
    }
    if (blocked) return;
    for (const k of keys) interactionSeen.set(k, { ts: now, hasRpcId: hasRpc });
    if (interactionSeen.size > 512) {
      for (const [k, v] of interactionSeen) {
        if (now - v.ts > INTERACTION_DEDUP_MS) interactionSeen.delete(k);
      }
    }
  }
  writeRecord(extra);
}

// 写 record 去重前的代理：mux 交互记录（审批/问题）走 writeRecordDedup，其余事件（状态/工具/结果/错误）直接走 writeRecord。

// inheritedAgents：热重载场景由壳传入旧实例的 agent 快照（null=首次启动/DSH
// 直接挂载，此时只依赖 ctx.agents.list() 与 agent/created 事件）。
// opts.skipHello：热重载/重装路径由壳传 true——hello 是**进程级一次性**握手
// 证书（用户约定：首次成功握手后再不重复发，直到 DSH 重启），重载不重发
// （新版本信息由后续每条记录携带，pet 已按记录逐条校验，不依赖 hello 刷新）。
export function apply(ctx, inheritedAgents = null, opts = null) {
  // 幂等：DSH 进程内可能多次调用插件 apply（实测每 ~2s 一次——hello 与
  // AgentStatus 成对、每 2s 一条的现象即源于此）。重复 apply 若每次都重写
  // hello/diagnostic、重连 mux、重挂监听，会：
  //   1) 刷屏 hello/diagnostic（消费端以为是多实例/热重载循环）；
  //   2) 反复 close/reconnect mux → approval/question 权威帧落在重连窗口
  //      时丢失 → 桌宠收不到事前 approval/request，只能事后补弹（用户报告的
  //      「审批变事后」）。
  // 因此同进程内只完整挂载一次；重复 apply 仅补挂 agent 快照（幂等）。
  if (applied) {
    if (inheritedAgents && Array.isArray(inheritedAgents)) {
      for (const agent of inheritedAgents) registerAgent(agent);
    }
    return;
  }
  applied = true;
  // hello 只在整个 DSH 进程的**首次**有效挂载写一次（进程级一次性，见上方
  // opts.skipHello 说明）。热重载（壳传 skipHello=true）不再重发。
  if (!(opts && opts.skipHello)) {
    writeBridgeHello();
  }
  // Make the resolved runtime destination observable for packaged builds.
  // This is intentionally emitted once per Bridge process and contains only
  // path metadata, never secrets or the full environment.
  writeRecord({
    event: "bridge/diagnostic",
    bridgeDir: bridgeDir(),
    instanceFile: INSTANCE_FILE,
    appData: process.env.APPDATA || "",
    home: os.homedir(),
    packaged: Boolean(process.pkg),
  });

  // The pet talks to this queue instead of calling session.prompt/cancel
  // directly.  Control therefore runs beside the real Agent and can cancel,
  // diagnose, and steer it without relying on the web API transport.
  startControlQueue(ctx);

  // 依赖 cordis 的 context 生命周期：agent/status 监听挂在 agent.ctx 上，
  // agent 销毁时随其 context 自动解绑，不累积 disposer。
  // 抽出 registerAgent 以便热重载后重放已存在的 agent（新实例不知道旧实例
  // 期间创建的 agent，必须枚举 ctx.agents.list() 重新挂接 status 监听）。
  function registerAgent(agent) {
    if (!agent) return;
    for (const id of [agent.id, agent.session?.id]) {
      if (id !== undefined && id !== null) {
        liveAgents.set(String(id), agent);
        knownSessions.add(String(id));
      }
    }
    // 运行时 agent.session 不携带 Web UI 的真实标题/项目名；先写基础记录，
    // 再通过 DSH 官方 apiProxy 的 session.list/workspace.list 获取真实投影。
    writeSessionMeta(agent, agent.session);
    scheduleSessionMetadataRefresh(ctx);
    // 注意：创建时不要写 idle——桌宠端本来就默认 idle 态。
    // 实测 dsh 创建 agent 后 4ms 内必发 running，此时若先写一条幻影 idle，
    // 会占住桌宠端 2 秒换帧节流位，把紧跟的真实 working 整个吞掉。
    const agentDisposer = agent.ctx.effect(() => {
      agentStates.set(agent, "idle");
      const stop = agent.ctx.on("agent/status", ({ status }) => {
        // running/idle 是连接生命周期的成功/切换信号，不能让上一轮
        // request-error 重试计数泄漏到下一轮。
        resetRetryConnection(String(agent.session?.id || agent.id || ""));
        agentStates.set(agent, status === "running" ? "working" : "idle");
        aggregateWrite();
      });
      // 模型请求错误：agent/request-error 是 cordis agent 上下文事件
      // （agent-loop 用 dispatch.waterfall 发出），不走 session/event——
      // 必须挂在 agent.ctx 上才能收到。供 stuck_detector 判断网络/鉴权/限流类根因。
      const stopErr = agent.ctx.on("agent/request-error", ({ failure }) => {
        const errCode = String((failure && failure.code) || "");
        const errMsg = String((failure && failure.message) || "");
        writeRecord({
          event: "agent/request-error",
          errorCode: errCode.slice(0, 48),
          errorMessage: truncate(errMsg),
        });
        const retrySessionKey = String(agent.session?.id || agent.id || "");
        // 只有同一 session 连续累计达到阈值才写高优先级提醒；每次
        // request-error 仍保留原始记录，便于诊断真实重试过程。
        if (isModelAccessError(errCode, errMsg) && noteRetryConnection(retrySessionKey)) {
          writeRecord({
            event: "model_access",
            errorCode: errCode.slice(0, 48) || "RATE_LIMIT",
            errorMessage: truncate(errMsg),
            sessionId: retrySessionKey,
            consecutiveRetryCount: retryConnectionStats.get(retrySessionKey)?.count || RETRY_EVENT_THRESHOLD,
          });
        } else if (!isModelAccessError(errCode, errMsg)) {
          resetRetryConnection(retrySessionKey);
        }
      });
      return () => {
        if (typeof stop === "function") stop();
        if (typeof stopErr === "function") stopErr();
        // agent 销毁：移出聚合并重算（全部退出时落一条 idle，桌宠回待机）
        agentStates.delete(agent);
        for (const [id, item] of liveAgents) {
          if (item === agent) liveAgents.delete(id);
        }
        aggregateWrite();
      };
    }, `${PLUGIN_ID}.agent()`);
    registerDisposer(agentDisposer); // 热重载时解除该 agent 的 status/error 监听
  }

  registerDisposer(ctx.on("agent/created", ({ agent }) => {
    registerAgent(agent);
  }));

  // 热重载后重放：旧实现期间已存在的 agent 不会再次触发 agent/created，
  // 必须主动枚举并重新挂接（否则状态聚合/控制在重载后失效直到新 agent 创建）。
  // ctx.agents 是可选的（发布版可能无此服务），缺失时静默跳过——首次启动
  // 路径不受影响（agent 逐个通过 agent/created 注册）。
  const replayedAgents = new Set();
  const replayAgent = (agent) => {
    if (!agent || replayedAgents.has(agent)) return; // 同实例重复列出只挂一次监听
    replayedAgents.add(agent);
    registerAgent(agent);
  };
  let agentsService = null;
  try {
    agentsService = typeof ctx.get === "function" ? ctx.get("agents", false) : ctx.agents;
  } catch (err) {
    console.warn(`[${PLUGIN_ID}] replay existing agents failed: ${String(err?.message || err)}`);
  }
  if (agentsService && typeof agentsService.list === "function") {
    try {
      for (const agent of agentsService.list()) replayAgent(agent);
    } catch (err) {
      console.warn(`[${PLUGIN_ID}] replay existing agents failed: ${String(err?.message || err)}`);
    }
  }
  // 快照兜底：DSH 发布版可能没有 ctx.agents 服务（或 list 返回空），此时用
  // 壳在热重载前从旧实例收集的 agent 对象快照重放，避免热重载后既有 agent
  // 的状态聚合/控制全部丢失（直到新 agent 创建才恢复）。
  if (Array.isArray(inheritedAgents)) {
    for (const agent of inheritedAgents) replayAgent(agent);
  }

  const cordisRequestSessions = new Map();
  registerDisposer(ctx.on("cordis/request-run", (request) => {
    if (!request || request.requiresApproval !== true) return;
    const requestId = String(request.requestId || "");
    cordisRequestSessions.set(requestId, String(request.agentId || ""));
    writeRecord({ event: "cordis/request-run", source: "dsh", agentId: String(request.agentId || ""), sessionId: String(request.agentId || ""), kind: "cordis", payload: request, requestId });
  }));
  registerDisposer(ctx.on("cordis/request-run-resolved", (resolved) => {
    if (!resolved) return;
    const requestId = String(resolved.requestId || "");
    const sessionId = cordisRequestSessions.get(requestId) || "";
    cordisRequestSessions.delete(requestId);
    writeRecord({ event: "cordis/request-run-resolved", source: "dsh", kind: "cordis", requestId, agentId: sessionId, sessionId, outcome: String(resolved.outcome || "") });
  }));

  // 过程汇报：session/event 在插件/根/agent 三层上下文都可达（实测验证）。
  // 注意 dsh 的工具调用不走独立 tool/call 事件——工具名在 assistant/message
  // 事件的 content 块里（type === "tool-call" 的块带 name 字段），
  // web UI 的工具卡片也是这么来的。assistant/message 每步只发一次，无流式重复。
  registerDisposer(ctx.on("session/event", (_session, event) => {
    try {
      if (!event) return;
      const type = event.type;
      const sessionId = sessionIdOf(_session, event);
      const agentName = agentLabelFor(sessionId);
      // 只有连续的 llm/retry 才属于同一轮连接异常；切换到任意其他
      // session/event（包括成功结果、工具调用和新的 turn）都开始新一轮统计。
      if (type !== "llm/retry") resetRetryConnection(sessionKeyOf(_session, event));
      // 标题通常在首条用户消息后异步生成；每个 session/event 都触发一次
      // 合并刷新，确保生成标题/改名后 Bridge 最终写出真实名称。
      scheduleSessionMetadataRefresh(ctx);

      // 若此 sessionId 尚未见过，尝试补发 session/meta
      if (!sessionMetaCache.has(sessionId) && _session) {
        writeSessionMeta(liveAgents.get(sessionId) || null, _session);
      }

      // Preserve the current user goal for goal-aware loop detection.  This is
      // still the same state event consumed by dsh_state, only enriched with a
      // bounded text field; full conversation history is never forwarded.
      if (type === "user/message") {
        const data = event.data || {};
        // DSH 的 UserMessage.source.kind 区分真人输入（kind="user"）与
        // agent.inject() 注入上下文（kind="plugin"：system-reminder/技能目录/
        // 记忆等，每轮多条约 1200 字）——转发给桌宠侧，让它只把真人消息当作
        // 「对话开始」触发，不被注入记录污染（dsh_state / 探索看门狗据此过滤）。
        const src = (data && data.source && data.source.kind) || "";
        writeRecord({
          event: "user/message",
          agentName,
          text: messageText(data),
          step: stepOf(event),
          sessionId,
          ...(src ? { sourceKind: src } : {}),
        });
      }

      // 1) 工具调用气泡（ask_user_question 除外——它有专门的 question/requested 常驻气泡）
      //    同时收集模型文本（截断）、记录待跟踪调用（用于卡住检测）。
      if (type === "assistant/message") {
        // data 形状：{ turn, step, message: { content: [...] } }（兼容 data 直接是消息）
        const data = event.data || {};
        const content = (data.message && data.message.content) || data.content;
        let texts = [];
        if (Array.isArray(content)) {
          for (const block of content) {
            if (!block) continue;
            if (block.type === "tool-call" && block.name) {
              if (block.name === QUESTION_TOOL) {
                // 兜底：assistant/message 的 tool-call 块也带 arguments，去重后补写
                writeQuestionRequest(
                  block.callId || block.id,
                  extractQuestions(block.arguments),
                  sessionId,
                );
                continue;
              }
              // 与下方独立 tool/call 事件同一条路径写入（按 callId 去重，避免双写）
              const cid = String(block.callId || block.id || "");
              noteToolCall(cid, block.name, block.arguments);
              noteLatestToolCall(block.name, block.arguments);
              if (cid && !writtenToolCallIds.has(cid)) {
                writtenToolCallIds.add(cid);
                writeRecord({
                  event: "tool/call",
                  agentName,
                  tool: String(block.name),
                  argsKey: summarizeArgs(block.arguments),
                  command: commandFromArgs(block.arguments),
                  callId: cid,
                  step: stepOf(event),
                  sessionId,
                });
              }
            } else if (block.type === "text" && typeof block.text === "string" && block.text.trim()) {
              texts.push(block.text);
            }
          }
        }
        // 带截断文本的 assistant/message 记录（供 stuck_detector 分析重试措辞）
        if (texts.length) {
          writeRecord({ event: "assistant/message", agentName, text: truncate(texts.join(" ").replace(/\s+/g, " ")), step: stepOf(event), sessionId });
        } else {
          writeStateEvent("assistant/message", stepOf(event), sessionId);
        }
        // 模型成功产出 = 重试已恢复 → 连续重试计数归零（见上方硬失败判定规则）
        noteTurnRecovery(sessionKeyOf(_session, event));
      }

      // 2) 审批请求：approval/asked 只是 DSH 的会话/审计信号（供 dsh_state 锁存
      //    waiting_approval），**不代表 Web UI 存在待确认的真实审批，绝不能由此
      //    驱动桌宠审批弹窗**——普通工具调用（如 pwsh 跑 Get-Location）一旦被宿主
      //    标成 approval/asked，桥接再升级成 approval/request，桌宠就会挂出一个
      //    永远等不到 approval/resolved 的 sticky 审批气泡。
      //    UI 层审批请求只由权威 mux 帧 approval/requested（带 rpcId+sessionId）
      //    产生（见下方 mux 中继）。approval/asked 仍经 STATE_EVENT_TYPES 转发为
      //    状态/审计事件，不写 approval/request。
      if (type === "approval/decided") {
        const data = event.data || {};
        writeInteractionResolved("approval", sessionId, { rpcId: data.rpcId, approvalId: data.approvalId, callId: data.callId }, data.outcome || data.decision || "approved");
      }
      // approval/asked：仅保留状态/审计转发（下方 STATE_EVENT_TYPES 统一处理），
      // 不再生成 approval/request，杜绝「普通/审计事件 → UI 审批弹窗」的误升级。

      // 2.5) 用户问题交互（阻塞型，与审批同等待遇）：ask_user_question 会暂停
      //     Agent 直到用户选择/回答。tool/call 是权威请求信号，tool/result 用
      //     message.callId 配对表示已解决（answer 已回填给 Agent）。
      //     同时记录卡住检测所需数据（工具名、参数指纹、成败、耗时）。
      if (type === "tool/call") {
        const d = event.data || {};
        // 工具调用 = 模型请求链已恢复推进 → 连续重试计数归零
        noteTurnRecovery(sessionKeyOf(_session, event));
        if (d.name === QUESTION_TOOL) {
          writeQuestionRequest(d.callId, extractQuestions(d.arguments), sessionId);
        }
        // 记录待跟踪调用（覆盖 assistant/message 兜底，去重写入）
        if (d.callId && d.name) {
          noteToolCall(d.callId, d.name, d.arguments);
          noteLatestToolCall(d.name, d.arguments);
          const cid = String(d.callId);
          if (!writtenToolCallIds.has(cid)) {
            writtenToolCallIds.add(cid);
            // 按需清理（防无限增长）
            if (writtenToolCallIds.size > 1024) writtenToolCallIds.clear();
            writeRecord({
              event: "tool/call",
              agentName,
              tool: String(d.name),
              argsKey: summarizeArgs(d.arguments),
              command: commandFromArgs(d.arguments),
              callId: cid,
              step: stepOf(event),
              sessionId,
            });
          }
        }
      } else if (type === "tool/result") {
        const d = event.data || {};
        // 与 toolResultInfo 同一取数路径：当前 dsh 版本 callId 也可能只挂在
        // message.source 下，只看 message.callId 会导致 resolveQuestion 永远
        // 收不到 callId——question/resolved 写不出，桌宠端提醒队列卡死。
        const callId = d.message && (d.message.callId || (d.message.source && d.message.source.callId));
        if (callId) resolveQuestion(callId, sessionId);
        // 不写 user_action(question_resolved) 兜底（合入 main 的 b62c1be）：
        // resolveQuestion 已写 question/resolved 并清 pending；裸 callId 匹配
        // 复合键（sessionId|callId）恒错，该分支恒不可达，纯属死代码。
        const info = toolResultInfo(d);
        const pending = consumeToolCall(info.callId) || {};
        const tool = pending.tool || "";
        const durationMs = pending.t0 ? Date.now() - pending.t0 : undefined;
        const argsKey = pending.argsKey || "";
        const timeout = /timeout|timed ?out|超时|ETIMEDOUT|ESOCKETTIMEDOUT/i.test(info.errorText || "") || /timeout/i.test(info.errorCode || "");
        const evidenceKey = `${sessionId}|${tool}|${argsKey}`;
        let evidenceStatus = "unavailable";
        let evidenceHash = "";
        if (!info.isError && info.resultText) {
          evidenceHash = createHash("sha256").update(info.resultText, "utf8").digest("hex").slice(0, 16);
          const previous = lastEvidenceByCallTarget.get(evidenceKey);
          evidenceStatus = previous === evidenceHash ? "same" : "new";
          lastEvidenceByCallTarget.set(evidenceKey, evidenceHash);
          if (lastEvidenceByCallTarget.size > 2048) lastEvidenceByCallTarget.clear();
        }
        writeRecord({
          event: "tool/result",
          agentName: agentLabelFor(sessionId),
          tool,
          argsKey,
          command: pending.command || "",
          callId: info.callId,
          ok: !info.isError,
          timeout: !!timeout,
          errorCode: info.errorCode,
          // 错误正文统一用 errorMessage（与 llm/retry / model_access / llm_error /
          // execution/failed 同一字段名），不再用并行的 errorText 别名。
          errorMessage: info.errorText,
          evidenceStatus,
          evidenceHash,
          ...(info.resultText ? { resultSummary: info.resultText } : {}),
          ...(durationMs !== undefined ? { durationMs } : {}),
          step: stepOf(event),
          sessionId,
        });
        // 硬失败判定：累计本轮工具成败（turn/end 时判定是否最终失败）
        noteTurnToolResult(
          sessionKeyOf(_session, event),
          !info.isError,
          info.errorCode,
          info.errorText,
        );
      }

      // 2.7) turn 开始：重置硬失败判定状态（新一轮从零计数；若上一轮 turn/end
      //      因异常漏发，这里兜底清掉残留统计，绝不跨 turn 累计）
      if (type === "turn/start") {
        resetTurnStats(_turnStats(sessionKeyOf(_session, event)));
      }

      // 2.75) 模型请求错误：agent/request-error 是 cordis agent 上下文事件，
      //      已在上方 agent/created 的 agent.ctx 监听里转发，不走 session/event——
      //      此处不处理，避免与 agent 上下文监听重复写盘。

      // 2.8) LLM 重试事件（retry 计数 + 失败原因，供 stuck_detector 判断 root cause）
      if (type === "llm/retry") {
        const d = event.data || {};
        const failure = d.failure || {};
        const errorCode = String(failure.code || "");
        const errorMessage = String(failure.message || "");
        writeRecord({
          event: "llm/retry",
          retry: typeof d.retry === "number" ? d.retry : 0,
          errorCode: errorCode.slice(0, 48),
          errorMessage: truncate(errorMessage),
          provider: String(d.provider || ""),
          step: stepOf(event),
          sessionId,
        });
        // 模型访问失败即时提醒：不等到 turn/end，LLM 重试时直接写 model_access 事件。
        // DSH 实测 errorCode 为 "RATE_LIMIT"（消息形如 "429: ..."），旧实现仅
        // 匹配 code==="429"，导致真实模型访问失败永远不触发。改用 isModelAccessError 判定。
        if (isModelAccessError(errorCode, errorMessage) &&
            noteRetryConnection(sessionKeyOf(_session, event))) {
          writeRecord({
            event: "model_access",
            errorCode: errorCode.slice(0, 48) || "RATE_LIMIT",
            errorMessage: truncate(errorMessage),
            sessionId,
            consecutiveRetryCount: RETRY_EVENT_THRESHOLD,
            retry: typeof d.retry === "number" ? d.retry : 0,
          });
        }
        // bad_response_status_code：AI API 返回 404/5xx 等 HTTP 错误，
        // 表示函数/模型不存在或 API 不可用。此类错误与限流不同，直接写入
        // llm_error 事件。errorCode 保留上游真实码（不再替换成 PI_AI_ERROR），
        // 分类语义由 errorKind 承载（errorKind=api → 弹窗走 llm_error.api 文案）。
        if (errorCode === "bad_response_status_code" &&
            noteRetryConnection(sessionKeyOf(_session, event))) {
          writeRecord({
            event: "llm_error",
            errorCode: errorCode.slice(0, 48),
            errorMessage: truncate(errorMessage),
            sessionId,
            retry: typeof d.retry === "number" ? d.retry : 0,
            errorKind: "api",
          });
        }
        // 累计本轮连续重试计数（恢复即清零；turn/end 判定「重试耗尽」时用）
        noteTurnRetry(sessionKeyOf(_session, event), errorCode);
      }

      // 2.85) 硬失败判定（execution/failed，脱敏，不经行为分析直接提醒）
      // 规则：DSH 的 turn/end 自带 data.reason.kind。只有 kind === "error"
      // （本轮真的以出错终止）才可能判硬失败：
      //   - 连续 llm/retry 达到阈值后 DSH 抛错 → 模型重试耗尽（failureType=model_retry_exhausted）
      //   - 本轮有工具失败且无任何成功，且 turn 以 error 结尾 → 工具最终失败（failureType=tool_failed）
      // 正常完成（completed）、被中止（aborted）、被阻塞（blocked）、触达
      // max-tokens 以及 reason 缺失的 turn/end，一律不写 execution/failed——
      // 中途抖动但最终恢复并正常收尾的 turn 绝不误报。
      // 只在 turn/end 时判定并写一条；错误码保留（判根因），错误正文不落盘。
      if (type === "turn/end") {
        const reason = (event.data && event.data.reason) || null;
        const failure = decideTurnEndFailure(reason, turnStatsMap.get(sessionKeyOf(_session, event)));
        if (failure) {
          writeRecord({ ...failure, sessionId });
        }
        _endTurnStats(sessionKeyOf(_session, event));
        resetRetryConnection(sessionKeyOf(_session, event));
      }

      // 3) 统一状态联动：转发 DSH 原始 session/event 类型为「简单事件」，
      //    桌宠侧 dsh_state.py 据此收敛为 thinking/working/waiting_approval/
      //    success/error（审批锁存依赖 approval/asked 与 approval/decided 成对出现）。
      //    注意 assistant/message、tool/call、tool/result、llm/retry 已在上方
      //    显式处理，不在 STATE_EVENT_TYPES 中，不会重复写入。
      //    转发时携带 step（step/start、turn/start 等），供行为模式检测按 step 去重。
      if (STATE_EVENT_TYPES.has(type)) {
      writeStateEvent(type, stepOf(event), sessionId, agentName);
      // 用户介入信号：approval/decided 或 thread_rolled_back 等用户主动干预事件
      // → 向桌宠发送 user_action 事件，关闭对应弹窗
      if (type === "approval/decided") {
        const data = event.data || {};
        writeRecord({
          event: "user_action",
          action: "approval_decided",
          decision: String(data.decision || ""),
          toolName: String(data.toolName || ""),
          rpcId: String(data.rpcId || ""),
          approvalId: String(data.approvalId || ""),
          sessionId,
          step: stepOf(event),
        });
      }
      } else if (WATCHDOG_EVENT_TYPES.has(type)) {
        const data = event.data || {};
        const extra = { event: type, step: stepOf(event), sessionId, agentName };
        if (type.includes("reasoning")) {
          extra.summary = truncate(data.text || data.content || data.reasoning || "");
        } else if (type.includes("search")) {
          extra.tool = String(data.tool || data.name || "web_search");
          extra.target = truncate(data.query || data.searchQuery || data.url || "");
        } else if (type.includes("command")) {
          extra.tool = String(data.tool || data.name || "shell");
          extra.target = truncate(data.command || data.cmd || "");
        }
        writeRecord(extra);
      }
    } catch {}
  }));

  // 交互式 mux 中继：连接 DSH 的 WebSocket mux 流，把带 rpcId 的审批/问题
  // 交互帧转发到 dsh.jsonl（桌宠据此弹可点选气泡并回写 /api/respond）。
  // 失败/断线只退避重连，绝不影响 DSH 主流程。
  muxConnect();
  registerDisposer(() => {
    // 先置位：禁止 onclose 回调复活重连（socket.close() 异步触发 onclose）；
    // 再清定时器、关连接。否则 dispose 后旧实例会在后台持续重连并双写盘。
    muxDisposed = true;
    if (muxTimer) { clearTimeout(muxTimer); muxTimer = null; }
    if (muxSocket) {
      try { muxSocket.close(); } catch {}
      muxSocket = null;
    }
    muxConnected = false;
  });
}

export { inject };
// Kept private-by-convention: package tests use this surface to exercise the
// control boundary without starting a DSH host or touching the real queue.
export const __controlTest = { controlAgent, handleControlRequest, liveAgents, knownSessions };
export const __messageTest = { createUserMessage };
export const __questionTest = {
  questionCallIdentity,
  pendingQuestionCallIds,
  pendingQuestionRpcPairs,
  pendingQuestionOrder,
  registerQuestionCall,
  forgetQuestionCall,
  muxQuestionRequestedRecord,
  muxQuestionResolvedRecord,
};
export const __retryTest = {
  threshold: RETRY_EVENT_THRESHOLD,
  reset: resetRetryConnection,
  note: noteRetryConnection,
  isModelAccess: isModelAccessError,
};
export const __hardFailureTest = {
  threshold: RETRY_EXHAUSTED_THRESHOLD,
  decideTurnEnd: decideTurnEndFailure,
  resetTurnStats,
  noteRetry: noteStatsRetry,
  recovery: noteStatsRecovery,
  noteToolResult: noteStatsToolResult,
};
export const __bridgeTest = {
  flush: flushPending,
  writeRecord,
  bridgeDir,
  instanceFile: INSTANCE_FILE,
};
