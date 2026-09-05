// dsh-pet 桌宠桥接插件（仅使用 DSH 提供的 LLM 服务，不主动联网）
// 订阅 DSH 的 agent 生命周期事件，追加写入共享桥目录的 dsh.jsonl，
// 桌宠侧的 DshMonitor 通过 byte-offset tail 读取（不回放历史）。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { randomUUID } from "node:crypto";
import { createUserMessage } from "@deepseek-ai/dsh-llm";

// The bridge uses DSH's canonical user-message envelope for steer/diagnosis.

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）
const PLUGIN_ID = "dsh-pet-bridge";
// These services are resolved by DSH when the plugin is loaded.  The bridge
// uses them only for the watchdog's isolated diagnosis request; normal event
// forwarding remains usable even when no model is configured.
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
const sessionMetaCache = new Map(); // sessionId → { label, projectName, agentName }
let lastState = null;

function aggregateWrite() {
  const anyBusy = [...agentStates.values()].some((s) => s === "working");
  const next = anyBusy ? "working" : "idle";
  if (next === lastState) return;
  lastState = next;
  writeRecord({ state: next });
}

// 判定是否为限流/429 错误。DSH 实测 errorCode 为 "RATE_LIMIT"（消息如 "429: ..."），
// 偶见直接 "429"。必须同时匹配 code 与 message，避免漏判。
function isRateLimitError(code, message) {
  const c = String(code || "").trim().toUpperCase();
  const m = String(message || "");
  if (c === "RATE_LIMIT" || c === "429" || c === "TOO_MANY_REQUESTS") return true;
  return m.startsWith("429") || /\b429\b/.test(m) || /rate.?limit/i.test(m);
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
  for await (const chunk of ctx.llm.stream({
    provider: selection.provider,
    model: selection.model,
    messages,
    maxTokens: 700,
    purpose: "dsh-pet-watchdog-replan",
    signal,
  })) {
    if (chunk?.type === "text-delta") output += String(chunk.text || "");
  }
  output = output.trim();
  if (!output) throw new Error("empty-diagnosis");
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
  let cancelInvoked = false;
  try {
    if (operation === "interrupt") {
      // Terminate means terminate: discard pending watchdog/user steering too.
      await agent.cancel("dsh-pet-watchdog", { keepInbox: false });
      cancelInvoked = true;
      if (await waitAgentIdle(agent)) {
        return { ok: true, operation, sessionId, phase: "cancelled", alreadyIdle: false, foundAgent: true, cancelInvoked };
      }
      return { ok: false, operation, sessionId, phase: "timeout", error: "cancel-timeout", foundAgent: true, cancelInvoked };
    }
    // Stop the active driver first.  keepInbox is essential: it prevents a
    // watchdog request from deleting ordinary queued Agent input.
    await agent.cancel("dsh-pet-watchdog-replan", { keepInbox: true });
    cancelInvoked = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.max(1000, Number(request.timeoutMs || 8000)));
    let plan;
    try {
      plan = await runBridgeDiagnosis(ctx, request, controller.signal);
    } finally {
      clearTimeout(timeout);
    }
    await agent.steer(createUserMessage({
      content: [{ type: "text", text: plan }],
      source: { kind: "plugin", plugin: PLUGIN_ID },
    }));
    return { ok: true, operation, sessionId, phase: "replanned", plan, foundAgent: true, cancelInvoked };
  } catch (err) {
    console.warn(`[${PLUGIN_ID}] control failed: ${String(err?.message || err)}`);
    return { ok: false, operation, sessionId, phase: "failed", error: "bridge-internal-error", foundAgent: true, cancelInvoked };
  }
}

function writeControlOutcome(id, request, result) {
  const controlResult = { source: "bridge", requestId: id, operation: request.operation,
    sessionId: request.sessionId, ok: !!result.ok, phase: result.phase || "",
    error: result.error || "", alreadyIdle: !!result.alreadyIdle,
    foundAgent: !!result.foundAgent, cancelInvoked: !!result.cancelInvoked };
  writeControlResponse(id, result);
  writeRecord({ event: "bridge/control-result", ...controlResult });
  writeRecord({ event: "watchdog/control-result", ...controlResult });
}

function startControlQueue(ctx) {
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
// 规则：DSH 已决定「本轮不再继续」的事件 → 直接提醒，不经行为分析。
//   - 模型请求重试耗尽（llm/retry 达到阈值）
//   - 本轮工具执行最终失败（有失败且无成功）
// 只在 turn/end 时判定并写一条脱敏记录（错误码保留、错误正文不落盘）。
const RETRY_EXHAUSTED_THRESHOLD = 4;
// 限流/连接重试只在同一 session 连续达到 5 次时提醒一次。
// 原始 llm/retry 仍然逐条转发，便于桌宠侧做详细诊断；这里只抑制高优先级
// rate_limit 事件，避免一次短暂抖动连续轰炸桌宠。
const RETRY_EVENT_THRESHOLD = 5;

// 每个 turn 的状态：sessionKey -> {retries, hadSuccess, hadFailure, lastErrorCode, lastErrorMessage, turnActive}
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
      lastErrorCode: "", lastErrorMessage: "", turnActive: false,
    });
  }
  return turnStatsMap.get(sessionKey);
}

function _endTurnStats(sessionKey) {
  turnStatsMap.delete(sessionKey);
}

// 记录一次工具结果对硬失败判定的影响（turn/start 重置，tool/result 累计）
function noteTurnToolResult(sessionKey, ok, errorCode, errorMessage) {
  const st = _turnStats(sessionKey);
  st.turnActive = true;
  if (ok) {
    st.hadSuccess = true;
  } else {
    st.hadFailure = true;
    if (errorCode) st.lastErrorCode = String(errorCode).slice(0, 48);
    if (errorMessage) st.lastErrorMessage = truncate(errorMessage);
  }
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

function extractSessionMeta(agent, session) {
  if (!session && !agent) return null;
  const sessionId = String(agent?.id || session?.id || "");
  if (!sessionId) return null;

  // 防御性字段提取，任何字段缺失都不会报错
  const label = session?.label ?? session?.title ?? session?.name ?? null;
  const projectName = session?.parent?.name ?? session?.project?.name ?? session?.workspace?.path ?? null;
  const agentName = agent?.name ?? agent?.displayName ?? null;

  // 构造显示标签
  const parts = ["DSH"];
  if (projectName) parts.push(projectName);
  if (label) parts.push(label);
  const displayLabel = parts.join(" · ");

  return { sessionId, label, projectName, agentName, displayLabel };
}

function writeSessionMeta(agent, session) {
  const meta = extractSessionMeta(agent, session);
  if (!meta) return;

  const sid = meta.sessionId;
  // 去重：仅当 label 或 projectName 变化时才重发
  const cached = sessionMetaCache.get(sid);
  if (cached && cached.displayLabel === meta.displayLabel) return;

  sessionMetaCache.set(sid, meta);
  writeRecord({
    type: "session/meta",
    sessionId: sid,
    label: meta.label,          // 原始对话名（writeRecord 自动补充 projectName）
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

function questionCallIdentity(callId, sessionId) {
  return `${String(sessionId || "")}|${String(callId || "")}`;
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
  const id = String(callId || "");
  const key = questionCallIdentity(id, sessionId);
  if (!id || pendingQuestionCallIds.has(key)) return; // 已写过，去重
  pendingQuestionCallIds.add(key);
  // 两个路径（tool/call + mux）都无条件写，由 writeRecordDedup 去重：
  // mux 正常时保留 rpcId 版本（可交互）；mux 不可用/连接失败时兜底写提示
  // （无按钮但至少弹窗出现，不会丢问题）。
  writeRecordDedup({
    event: "question/requested",
    callId: id,
    sessionId: String(sessionId || ""),
    questions,
  });
}

function resolveQuestion(callId, sessionId) {
  const id = String(callId || "");
  const key = questionCallIdentity(id, sessionId);
  if (!id || !pendingQuestionCallIds.has(key)) return;
  pendingQuestionCallIds.delete(key);
  // 收尾记录不 gate mux：重复的 question/resolved 无害（桌宠幂等），
  // 但若 mux 在问题中途才连上、丢了对应的 resolved 帧，这里必须兜底写，
  // 否则桌宠会卡死在 waiting_question。
  writeRecord({
    event: "question/resolved",
    callId: id,
    sessionId: String(sessionId || ""),
  });
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

// DSH 可能跑在 3080（web 默认）或 38080（端口被占时的避让），也可能由
// DSH_PORT 指定——全部作为候选，逐个尝试，任一连上即可（与桌宠端一致）。
function muxCandidatePorts() {
  const ports = [];
  if (process.env.DSH_PORT) ports.push(Number(process.env.DSH_PORT));
  ports.push(3080, 38080);
  return [...new Set(ports.filter((p) => Number.isInteger(p) && p > 0))];
}

function muxScheduleReconnect() {
  if (muxTimer) clearTimeout(muxTimer);
  muxTimer = setTimeout(() => {
    muxTimer = null;
    muxConnect();
  }, muxReconnectMs);
  muxReconnectMs = Math.min(muxReconnectMs * 2, 15000);
}

function muxConnect() {
  if (typeof WebSocket === "undefined") return; // 旧 Node：无 WS，降级为纯提示
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
        writeRecordDedup({ event: "question/requested", rpcId: msg.rpcId, sessionId: p.sessionId, questions: p.questions });
      } else if (p.type === "question/resolved") {
        const questionRpcId = p.questionRpcId || msg.rpcId;
        writeRecord({ event: "question/resolved", rpcId: questionRpcId, sessionId: p.sessionId, outcome: p.outcome });
        writeInteractionResolved("question", p.sessionId, { rpcId: questionRpcId }, p.outcome || "answered");
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

function flushPending() {
  flushTimer = null;
  if (writeQueue.length === 0) return;
  const batch = writeQueue.splice(0, writeQueue.length).join("");
  try {
    const dir = bridgeDir();
    fs.mkdirSync(dir, { recursive: true });
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
    // 静默失败：桥接是锦上添花，绝不能影响 DSH 本体
  }
}

function writeRecord(extra) {
  try {
    // 从 sessionMetaCache 补充 projectName / label（会话所属项目名与对话名）
    const sid = extra.sessionId;
    if (sid) {
      const meta = sessionMetaCache.get(sid);
      if (meta) {
        if (meta.projectName) extra.projectName = meta.projectName;
        if (meta.label) extra.label = meta.label;
      }
    }
    writeQueue.push(
      JSON.stringify({ ts: Date.now() / 1000, agent: "dsh", event: "AgentStatus", ...extra }) + "\n",
    );
    if (flushTimer === null) {
      flushTimer = setTimeout(flushPending, FLUSH_DELAY_MS);
      if (flushTimer.unref) flushTimer.unref(); // 不阻止 DSH 进程退出
    }
  } catch {
    // 入队失败也静默：绝不影响 DSH
  }
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
    // tool+command 作为降级去重键（同 agent 的同一命令审批不应重复）
    const tool = extra.toolName || extra.tool || "";
    const cmd = extra.command || "";
    if (tool || cmd) keys.push(`ap:tc:${tool}|${cmd}`);
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

export function apply(ctx) {
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
  ctx.on("agent/created", ({ agent }) => {
    if (!agent) return;
    for (const id of [agent.id, agent.session?.id]) {
      if (id !== undefined && id !== null) {
        liveAgents.set(String(id), agent);
        knownSessions.add(String(id));
      }
    }
    // 输出会话元数据供桌宠显示
    writeSessionMeta(agent, agent.session);
    // 注意：创建时不要写 idle——桌宠端本来就默认 idle 态。
    // 实测 dsh 创建 agent 后 4ms 内必发 running，此时若先写一条幻影 idle，
    // 会占住桌宠端 2 秒换帧节流位，把紧跟的真实 working 整个吞掉。
    agent.ctx.effect(() => {
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
        if (isRateLimitError(errCode, errMsg) && noteRetryConnection(retrySessionKey)) {
          writeRecord({
            event: "rate_limit",
            errorCode: errCode.slice(0, 48) || "RATE_LIMIT",
            errorMessage: truncate(errMsg),
            sessionId: retrySessionKey,
            consecutiveRetryCount: retryConnectionStats.get(retrySessionKey)?.count || RETRY_EVENT_THRESHOLD,
          });
        } else if (!isRateLimitError(errCode, errMsg)) {
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
  });

  const cordisRequestSessions = new Map();
  ctx.on("cordis/request-run", (request) => {
    if (!request || request.requiresApproval !== true) return;
    const requestId = String(request.requestId || "");
    cordisRequestSessions.set(requestId, String(request.agentId || ""));
    writeRecord({ event: "cordis/request-run", source: "dsh", agentId: String(request.agentId || ""), sessionId: String(request.agentId || ""), kind: "cordis", payload: request, requestId });
  });
  ctx.on("cordis/request-run-resolved", (resolved) => {
    if (!resolved) return;
    const requestId = String(resolved.requestId || "");
    const sessionId = cordisRequestSessions.get(requestId) || "";
    cordisRequestSessions.delete(requestId);
    writeRecord({ event: "cordis/request-run-resolved", source: "dsh", kind: "cordis", requestId, agentId: sessionId, sessionId, outcome: String(resolved.outcome || "") });
  });

  // 过程汇报：session/event 在插件/根/agent 三层上下文都可达（实测验证）。
  // 注意 dsh 的工具调用不走独立 tool/call 事件——工具名在 assistant/message
  // 事件的 content 块里（type === "tool-call" 的块带 name 字段），
  // web UI 的工具卡片也是这么来的。assistant/message 每步只发一次，无流式重复。
  ctx.on("session/event", (_session, event) => {
    try {
      if (!event) return;
      const type = event.type;
      const sessionId = sessionIdOf(_session, event);
      const agentName = agentLabelFor(sessionId);
      // 只有连续的 llm/retry 才属于同一轮连接异常；切换到任意其他
      // session/event（包括成功结果、工具调用和新的 turn）都开始新一轮统计。
      if (type !== "llm/retry") resetRetryConnection(sessionKeyOf(_session, event));

      // 若此 sessionId 尚未见过，尝试补发 session/meta
      if (!sessionMetaCache.has(sessionId) && _session) {
        writeSessionMeta(liveAgents.get(sessionId) || null, _session);
      }

      // Preserve the current user goal for goal-aware loop detection.  This is
      // still the same state event consumed by dsh_state, only enriched with a
      // bounded text field; full conversation history is never forwarded.
      if (type === "user/message") {
        writeRecord({
          event: "user/message",
          agentName,
          text: messageText(event.data || {}),
          step: stepOf(event),
          sessionId,
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
        const callId = d.message && d.message.callId;
        if (callId) resolveQuestion(callId, sessionId);
        // 用户介入信号：ask_user_question 回答后
        if (callId && pendingQuestionCallIds.has(String(callId))) {
          writeRecord({
            event: "user_action",
            action: "question_resolved",
            callId: String(callId),
            sessionId,
          });
        }
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
          errorText: info.errorText,
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

      // 2.7) turn 开始：重置硬失败判定状态（新一轮从零计数）
      if (type === "turn/start") {
        _turnStats(sessionKeyOf(_session, event)).turnActive = true;
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
        // 429 限流即时提醒：不等到 turn/end，LLM 重试时直接写 rate_limit 事件。
        // DSH 实测 errorCode 为 "RATE_LIMIT"（消息形如 "429: ..."），旧实现仅
        // 匹配 code==="429"，导致真实限流永远不触发。改用 isRateLimitError 判定。
        if (isRateLimitError(errorCode, errorMessage) &&
            noteRetryConnection(sessionKeyOf(_session, event))) {
          writeRecord({
            event: "rate_limit",
            errorCode: errorCode.slice(0, 48) || "RATE_LIMIT",
            errorMessage: truncate(errorMessage),
            sessionId,
            consecutiveRetryCount: RETRY_EVENT_THRESHOLD,
            retry: typeof d.retry === "number" ? d.retry : 0,
          });
        }
        // PI_AI_ERROR（bad_response_status_code）：AI API 返回 404/5xx 等 HTTP 错误，
        // 表示函数/模型不存在或 API 不可用。此类错误与限流不同，直接写入 llm_error 事件。
        if (errorCode === "bad_response_status_code" &&
            noteRetryConnection(sessionKeyOf(_session, event))) {
          writeRecord({
            event: "llm_error",
            errorCode: "PI_AI_ERROR",
            errorMessage: truncate(errorMessage),
            sessionId,
            retry: typeof d.retry === "number" ? d.retry : 0,
          });
        }
        // 累计本轮重试计数（供 turn/end 时判定「重试耗尽」硬失败）
        const st = _turnStats(sessionKeyOf(_session, event));
        if (st) st.retries++;
      }

      // 2.85) 硬失败判定（execution/failed，脱敏，不经行为分析直接提醒）
      // 规则：DSH 已决定「本轮不再继续」的事件 → 直接提醒，不走 stuck_detector。
      //   - llm/retry 重试耗尽（>= 阈值）
      //   - 本轮有工具失败且无任何成功（工具执行最终失败）
      // 只在 turn/end 时判定并写一条；错误码保留（判根因），错误正文不落盘。
      if (type === "turn/end") {
        const st = _turnStats(sessionKeyOf(_session, event));
        if (st && st.turnActive) {
          const retryExhausted = st.retries >= RETRY_EXHAUSTED_THRESHOLD;
          const toolFailed = st.hadFailure && !st.hadSuccess;
          if (retryExhausted || toolFailed) {
            writeRecord({
              event: "execution/failed",
              source: retryExhausted ? "model_request" : "tool",
              retryExhausted: !!retryExhausted,
              retries: st.retries,
              errorCode: st.lastErrorCode || "",
              errorMessage: st.lastErrorMessage || "",
              sessionId,
            });
          }
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
  });

  // 交互式 mux 中继：连接 DSH 的 WebSocket mux 流，把带 rpcId 的审批/问题
  // 交互帧转发到 dsh.jsonl（桌宠据此弹可点选气泡并回写 /api/respond）。
  // 失败/断线只退避重连，绝不影响 DSH 主流程。
  muxConnect();
}

export { inject };
// Kept private-by-convention: package tests use this surface to exercise the
// control boundary without starting a DSH host or touching the real queue.
export const __controlTest = { controlAgent, handleControlRequest, liveAgents, knownSessions };
export const __retryTest = {
  threshold: RETRY_EVENT_THRESHOLD,
  reset: resetRetryConnection,
  note: noteRetryConnection,
};
