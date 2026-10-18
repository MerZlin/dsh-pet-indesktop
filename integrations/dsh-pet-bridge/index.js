// dsh-pet 桌宠桥接插件（纯本地写盘，不主动联网、不订阅 mux、不读写控制队列）
// 订阅 DSH 的 agent 生命周期事件，追加写入共享桥目录的 dsh-{pid}.jsonl
//（多实例分区；消费端 glob dsh*.jsonl，兼容旧单文件 dsh.jsonl），
// 桌宠侧的 DshMonitor 通过 byte-offset tail 读取（不回放历史）。
//
// 脱敏口径（#226，2026-10 减法）：不落任何明文内容——用户消息正文、模型回复
// 正文、工具命令/参数指纹、工具结果摘要一律不写盘；只写事件名、状态、工具名、
// 错误码、会话/调用身份等元数据。
//
// 正文开关（assistantText，**默认关**）：这是本机临时口径，不是上游口径。
// 关 = 上段 #226 行为逐字节不变；开 = 在 assistant/message 状态事件上补写
// text 字段（助手回复正文，截断到 TEXT_MAX）。依据见
// docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md 的事件表：assistant/message
// 本就约定携带 text，桌宠侧 pet/dsh_state.py 也按 record["text"] 读取。用户消息
// 正文、工具命令/参数、结果摘要仍然一律不落盘——开关只放行助手回复正文。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";

// ===== 零依赖红线 =====
// 本插件必须保持零外部依赖：profile 经 pnpm 的 link: 协议链接到本目录，
// pnpm 不会安装被链接包自己的依赖；而链接目标常常是打包版桌宠
// _internal 内的副本（CI 构建不带 node_modules）。一旦此处声明运行时依赖，
// 依赖解析失败会让 Cordis 插件树初始化整体抛错、DSH 无法启动（2026-09 事故：
// 作者与多用户 dsh 全 profile 起不来）。

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）
const PLUGIN_ID = "dsh-pet-bridge";
// 减法后本插件不消费任何 DSH 注入服务（看门狗诊断已删）：保持空 inject，
// 事件转发在无模型配置时同样可用。
const inject = [];

// 进程内状态去重 + 多 Agent 聚合：
// 1) dsh 在 agent 创建/状态切换瞬间会抖动出重复 idle（实测 idle→working 仅隔
//    4ms），重复聚合状态不落盘——否则桌宠端 2 秒换帧节流会吞掉真实 working。
// 2) 必须按 agent 分别跟踪再聚合（任一在忙 = 忙）：dsh 可并发多个 agent
//   （子代理/多会话），全局单值去重会让先完成的 agent 把还在干活的顶成 idle。
const agentStates = new Map(); // agent 对象 → "working" | "idle"
const liveAgents = new Map(); // agent/session id → agent object
const sessionMetaCache = new Map(); // sessionId → { sessionName, projectName, agentName }
let metadataRefreshPromise = null;
let metadataRefreshTimer = null;
let lastState = null;

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

// 过程汇报：工具调用事件（state 不变，只带 tool 字段，桌宠端据此弹「正在跑命令…」）
// 注：工具名在 assistant/message 的 tool-call 块与独立 tool/call 事件中均可获得，
// 统一按 callId 去重写入（见下方 session/event 处理），不再单独 writeTool。

const TEXT_MAX = 300;

// 正文开关运行时状态：只在 apply() 里按 profile 的插件 config 赋值一次。
// 模块级默认 false —— 未配置（旧 profile、打包副本未改配置）时保持 #226 脱敏。
let assistantTextEnabled = false;

// 开关解析：profile 插件 config 优先，环境变量仅作兜底。只认显式真值，
// 任何取不到/认不出的值都退回 false（脱敏是安全侧默认）。
function readAssistantTextSwitch(config) {
  const raw = config && config.assistantText !== undefined
    ? config.assistantText
    : process.env.DSH_PET_BRIDGE_ASSISTANT_TEXT;
  if (raw === true) return true;
  if (raw === false || raw === undefined || raw === null) return false;
  const s = String(raw).trim().toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

// 从 assistant/message 事件取模型回复正文。真实形状（对 DSH 0.2.0-rc.2 会话
// 存档实测）：data.message.content 是块数组，回复正文在 type === "text" 的块里，
// 同数组还混着 reasoning / tool-call 块——只取 text 块，别把思考与工具参数当正文。
// 兼容 data.text / event.text 旧形状作为兜底。
function assistantTextOf(event) {
  const data = (event && event.data) || {};
  const message = data.message;
  const blocks = (message && message.content) || data.content;
  const parts = [];
  if (Array.isArray(blocks)) {
    for (const block of blocks) {
      if (!block || block.type !== "text") continue;
      if (typeof block.text === "string" && block.text) parts.push(block.text);
    }
  }
  if (!parts.length) {
    const flat = (typeof data.text === "string" && data.text)
      || (typeof (event && event.text) === "string" && event.text)
      || "";
    if (flat) parts.push(flat);
  }
  return parts.join("\n");
}

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

// turn/end 的终止原因 kind（completed / error / aborted / blocked / max-tokens）。
// 只取短枚举字符串，非字符串或缺失返回空串（不写字段）。
function turnEndReasonKind(event) {
  const reason = event && event.data && event.data.reason;
  const kind = reason && typeof reason.kind === "string" ? reason.kind.trim() : "";
  return kind.length <= 32 ? kind : "";
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

function truncate(s, max = TEXT_MAX) {
  if (typeof s !== "string") s = String(s || "");
  return s.length > max ? s.slice(0, max) : s;
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

}

function toolResultInfo(data) {
  const message = (data && data.message) || {};
  const callId = message.callId || (message.source && message.source.callId) || "";
  let isError = false, errorText = "", errorCode = "";
  const content = message.content;
  if (Array.isArray(content)) {
    for (const block of content) {
      if (!block || typeof block !== "object") continue;
      if (block.type === "tool-result") {
        if (block.isError) isError = true;
        const c = block.content;
        // 脱敏：只取错误正文（截断），成功结果正文不读不落盘
        if (block.isError && c !== undefined && c !== null) {
          errorText = typeof c === "string" ? c : JSON.stringify(c);
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
    errorCode: errorCode.slice(0, 48),
  };
}

const pendingTools = new Map(); // callId -> { tool, t0 }（tool/result 配对取工具名用）
const writtenToolCallIds = new Set(); // callId -> 已写入过 tool/call 记录（去重）

function noteToolCall(callId, tool) {
  if (!callId || pendingTools.has(callId)) return;
  pendingTools.set(callId, { tool: String(tool || ""), t0: Date.now() });
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

// Extract the step identifier from a DSH session/event.
// DSH events carry { turn, step, ... } in event.data. The pet side keeps step as an
// ordering/display field (activity bubble template {step}); the behavior-pattern
// detector that used it to deduplicate parallel tool calls was removed in the
// 2026-10 subtraction, so no control flow depends on it anymore.
function stepOf(event) {
  const data = (event && event.data) || {};
  if (data && data.step !== undefined && data.step !== null) return data.step;
  if (data && data.turn !== undefined && data.turn !== null) return `turn:${data.turn}`;
  return null;
}

// 透传该事件所属**回合**的原始 turn 号（字面就是 event.data.turn；同一回合被多个写者
// 各写一份时取值相同）：桌宠侧（pet/dsh_state.py::TurnCompletionTracker）用它把整封镜像
// 信封（两个写者各写一份 user/message + turn/start + turn/end）绑到同一个完成身份上，
// 同一回合绝不提醒两次。只做透传：取不到就整字段不写（旧桥接 / 无 turn 的事件保持
// 原行为）。
function sourceTurnOf(event) {
  const data = (event && event.data) || {};
  if (data && data.turn !== undefined && data.turn !== null) return String(data.turn);
  return "";
}

function sessionIdOf(session, event) {
  if (session && session.id) return String(session.id);
  const data = (event && event.data) || {};
  if (data && data.sessionId) return String(data.sessionId);
  if (data && data.session_id) return String(data.session_id);
  if (data && data.turn !== undefined && data.turn !== null) return `turn:${data.turn}`;
  return "session:unknown";
}

function writeStateEvent(type, step, sessionId, agentName = "", reasonKind = "", sourceTurn = "", text = "") {
  const extra = { event: type };
  if (step !== undefined && step !== null) extra.step = step;
  // turn/end 携带终止原因（只落 kind，不落错误正文）：桌宠据此让用户中止的回合
  // 静默收尾，不报「完成」。字段缺失 = 旧桥接，桌宠保持原行为。
  if (reasonKind) extra.reasonKind = reasonKind;
  // 该事件所属回合的原始 turn 号（见 sourceTurnOf）：桌宠据此把同一回合的各写者副本
  // 绑到一个完成身份上。取不到就不写——旧桥/无 turn 的事件行为不变。
  if (sourceTurn) extra.sourceTurn = sourceTurn;
  if (sessionId) extra.sessionId = sessionId;
  if (agentName) extra.agentName = agentName;
  // 助手回复正文（仅 assistantText 开关打开时由调用方传入；关闭时恒为空串，
  // 记录与 #226 逐字节一致）。字段名 text 是 docs/DSH-BRIDGE-PET-EVENT-CONTRACT
  // 事件表里 assistant/message 既有约定的名字，桌宠侧 pet/dsh_state.py 按它读取。
  if (text) extra.text = text;
  writeRecord(extra);
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

function registerQuestionCall(callId, sessionId) {
  const id = String(callId || "");
  if (!id || pendingQuestionCallIds.has(questionCallIdentity(id, sessionId))) return false;
  pendingQuestionCallIds.add(questionCallIdentity(id, sessionId));
  return true;
}

function forgetQuestionCall(callId, sessionId) {
  const id = String(callId || "");
  if (!id) return;
  pendingQuestionCallIds.delete(questionCallIdentity(id, sessionId));
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
  // 纯提示路径：assistant/message 与独立 tool/call 两处都可能发现
  // ask_user_question，由 writeRecordDedup 去重。
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
  // 收尾记录：重复的 question/resolved 无害（桌宠幂等），但必须写——
  // 否则桌宠会卡死在 waiting_question。
  writeRecord({
    event: "question/resolved",
    callId: id,
    sessionId: String(sessionId || ""),
  });
}

// ===== 写盘去抖（batch flush） =====
// 之前每条事件都立即 mkdirSync+statSync+appendFileSync 同步写盘；DSH 会话忙碌
// （流式/工具密集）时，这些逐事件同步文件 I/O 会卡住 DSH 的 Node 主线程，连带
// 桌宠感知卡顿。改为：事件先入内存队列，合并到一个延迟 flush 里一次性落盘——
// 一个节流窗口内无论来多少事件，DSH 主线程都只做一次文件写。

// 每个 DSH 实例写自己的文件（dsh-{pid}.jsonl），避免 Windows 多实例并行写
// 同一文件的数据行交织；consumers 读取全部 dsh-*.jsonl。
const INSTANCE_FILE = `dsh-${process.pid}.jsonl`;

// 本插件「这次启动」的实例标识（信封字段，**不是** DSH 字段）。多 DSH 实例并行时
// 桌宠靠它把各实例的状态/交互分开：pid 会被系统复用（同一 pid 重启、同一路径被
// 轮转重建都不是同一个写者），单看 pid 不足以判定归属；本 UUID 每次插件加载重新
// 生成，只在这一代写者内稳定。由插件自己盖（调用方传入同名字段一律被覆盖）。
const BOOT_ID = randomUUID();

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

function encodeRecord(extra) {
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
  // inst 放在 ...extra **之后**：写者归属由插件自己盖，调用方（含旧版载荷形状）
  // 传入同名字段不得顶掉它——顶掉就等于把归属交给事件内容，多实例隔离当场失效。
  return JSON.stringify({ ts: Date.now() / 1000, agent: "dsh", event: "AgentStatus", ...extra, inst: BOOT_ID }) + "\n";
}

function writeRecord(extra) {
  try {
    writeQueue.push(encodeRecord(extra));
    if (flushTimer === null) {
      flushTimer = setTimeout(flushPending, FLUSH_DELAY_MS);
      if (flushTimer.unref) flushTimer.unref(); // 不阻止 DSH 进程退出
    }
  } catch {
    // 入队失败也静默：绝不影响 DSH
  }
}

// ===== 问题写盘去重 =====
// question/requested 由 assistant/message 的 tool-call 块与独立 tool/call 事件
// 双通道产生：短窗口内同一条问题只落一条记录，杜绝重复气泡。
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
  if (ev === "question/requested" || ev === "question/resolved") {
    if (extra.rpcId) keys.push(`qu:${extra.rpcId}`);
    if (extra.callId) keys.push(`qu:call:${extra.sessionId || ""}:${extra.callId}`);
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

// 写 record 去重前的代理：需要按 callId 去重的交互记录（问题请求在 assistant/message
// 与独立 tool/call 两处都会出现）走 writeRecordDedup，其余事件直接走 writeRecord。

export function apply(ctx, config = {}) {
  // 正文开关（默认关）：profile 的插件 config 里写 assistantText: true 才放行
  // 助手回复正文落盘。不传 config（旧 profile）时保持 #226 脱敏口径不变。
  assistantTextEnabled = readAssistantTextSwitch(config);

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

  // 依赖 cordis 的 context 生命周期：agent/status 监听挂在 agent.ctx 上，
  // agent 销毁时随其 context 自动解绑，不累积 disposer。
  ctx.on("agent/created", ({ agent }) => {
    if (!agent) return;
    for (const id of [agent.id, agent.session?.id]) {
      if (id !== undefined && id !== null) {
        liveAgents.set(String(id), agent);
      }
    }
    // 运行时 agent.session 不携带 Web UI 的真实标题/项目名；先写基础记录，
    // 再通过 DSH 官方 apiProxy 的 session.list/workspace.list 获取真实投影。
    writeSessionMeta(agent, agent.session);
    scheduleSessionMetadataRefresh(ctx);
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
      // 必须挂在 agent.ctx 上才能收到。供桌宠侧识别网络/鉴权/限流类故障。
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
      // 标题通常在首条用户消息后异步生成；每个 session/event 都触发一次
      // 合并刷新，确保生成标题/改名后 Bridge 最终写出真实名称。
      scheduleSessionMetadataRefresh(ctx);

      // 若此 sessionId 尚未见过，尝试补发 session/meta
      if (!sessionMetaCache.has(sessionId) && _session) {
        writeSessionMeta(liveAgents.get(sessionId) || null, _session);
      }

      if (type === "user/message") {
        const data = event.data || {};
        // DSH 的 UserMessage.source.kind 区分真人输入（kind="user"）与
        // agent.inject() 注入上下文（kind="plugin"：system-reminder/技能目录/
        // 记忆等，每轮多条约 1200 字）——转发给桌宠侧，让它只把真人消息当作
        // 「对话开始」触发，不被注入记录污染（dsh_state 收敛器据此过滤）。
        // 脱敏：消息正文不落盘，只写事件名 + 身份字段。
        const src = (data && data.source && data.source.kind) || "";
        const sourceTurn = sourceTurnOf(event);
        writeRecord({
          event: "user/message",
          agentName,
          step: stepOf(event),
          sessionId,
          ...(src ? { sourceKind: src } : {}),
          ...(sourceTurn ? { sourceTurn } : {}),
        });
      }

      // 1) 工具调用气泡（ask_user_question 除外——它有专门的 question/requested 常驻气泡）
      if (type === "assistant/message") {
        // data 形状：{ turn, step, message: { content: [...] } }（兼容 data 直接是消息）
        const data = event.data || {};
        const content = (data.message && data.message.content) || data.content;
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
              noteToolCall(cid, block.name);
              if (cid && !writtenToolCallIds.has(cid)) {
                writtenToolCallIds.add(cid);
                // 脱敏：只写工具名/身份/序号，命令与参数指纹不落盘
                writeRecord({
                  event: "tool/call",
                  agentName,
                  tool: String(block.name),
                  callId: cid,
                  step: stepOf(event),
                  sessionId,
                });
              }
            }
          }
        }
        // 模型回复正文：默认仍不落盘（脱敏）。仅当助手正文开关打开时补写 text
        // 字段；开关关闭时下面这行与 #226 完全一致，记录里不会出现 text。
        const bodyText = assistantTextEnabled
          ? truncate(assistantTextOf(event), TEXT_MAX)
          : "";
        writeStateEvent("assistant/message", stepOf(event), sessionId, "", "",
          sourceTurnOf(event), bodyText);
        // 模型成功产出 = 重试已恢复 → 连续重试计数归零（见上方硬失败判定规则）
        noteTurnRecovery(sessionKeyOf(_session, event));
      }

      // 2) 审批请求：approval/asked 只是 DSH 的会话/审计信号，**不代表 Web UI 存在
      //    待确认的真实审批，绝不能由此驱动桌宠审批弹窗**——普通工具调用（如 pwsh
      //    跑 Get-Location）一旦被宿主标成 approval/asked，桥接再升级成
      //    approval/request，桌宠就会挂出一个永远等不到 approval/resolved 的 sticky
      //    审批气泡。
      //    减法后桌宠侧不再有审批回写（mux 中继已删）：approval/asked 只经
      //    STATE_EVENT_TYPES 转发为状态/审计事件，不写 approval/request。桌宠收敛器
      //    也不拿它锁存 waiting_approval（普通工具调用同样带这个标记）——权威锁存源
      //    是 cordis/request-run 与 approval/request，见 pet/dsh_state.py 的事件表。
      if (type === "approval/decided") {
        const data = event.data || {};
        writeInteractionResolved("approval", sessionId, { rpcId: data.rpcId, approvalId: data.approvalId, callId: data.callId }, data.outcome || data.decision || "approved");
      }
      // approval/asked：仅保留状态/审计转发（下方 STATE_EVENT_TYPES 统一处理），
      // 不再生成 approval/request，杜绝「普通/审计事件 → UI 审批弹窗」的误升级。

      // 2.5) 用户问题交互（阻塞型，与审批同等待遇）：ask_user_question 会暂停
      //     Agent 直到用户选择/回答。tool/call 是权威请求信号，tool/result 用
      //     message.callId 配对表示已解决（answer 已回填给 Agent）。
      if (type === "tool/call") {
        const d = event.data || {};
        // 工具调用 = 模型请求链已恢复推进 → 连续重试计数归零
        noteTurnRecovery(sessionKeyOf(_session, event));
        if (d.name === QUESTION_TOOL) {
          writeQuestionRequest(d.callId, extractQuestions(d.arguments), sessionId);
        }
        // 记录待跟踪调用（覆盖 assistant/message 兜底，去重写入）
        if (d.callId && d.name) {
          noteToolCall(d.callId, d.name);
          const cid = String(d.callId);
          if (!writtenToolCallIds.has(cid)) {
            writtenToolCallIds.add(cid);
            // 按需清理（防无限增长）
            if (writtenToolCallIds.size > 1024) writtenToolCallIds.clear();
            // 脱敏：只写工具名/身份/序号，命令与参数指纹不落盘
            writeRecord({
              event: "tool/call",
              agentName,
              tool: String(d.name),
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
        // 问题收尾的 question/resolved 由 resolveQuestion 内部负责写盘（桌宠按它
        // 关闭问题气泡）。这里不再补写 user_action 兜底：resolveQuestion 的第一
        // 动作就是删掉 pendingQuestionCallIds 里的复合键 sessionId|callId，紧随
        // 其后的裸 callId 查询恒为 False，那段写盘永远不可达。
        const info = toolResultInfo(d);
        const pending = consumeToolCall(info.callId) || {};
        writeRecord({
          event: "tool/result",
          agentName: agentLabelFor(sessionId),
          tool: pending.tool || "",
          callId: info.callId,
          ok: !info.isError,
          errorCode: info.errorCode,
          // 错误正文统一用 errorMessage（与 llm/retry / model_access / llm_error /
          // execution/failed 同一字段名），不再用并行的 errorText 别名。
          // 脱敏：结果正文/命令/耗时/证据指纹一律不落盘。
          errorMessage: info.errorText,
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

      // 2.8) LLM 重试事件（retry 计数 + 失败原因，供桌宠侧识别根因）
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
      //    success/error（审批锁存由 cordis/request-run 与 approval/request 进入、
      //    approval/decided 解锁；裸 approval/asked 不参与收敛——普通工具调用也会
      //    被宿主标上它，见 pet/dsh_state.py 的事件表）。
      //    注意 assistant/message、tool/call、tool/result、llm/retry 已在上方
      //    显式处理，不在 STATE_EVENT_TYPES 中，不会重复写入。
      //    转发时携带 step（step/start、turn/start 等）：行为模式检测已删，step
      //    只剩事件序号用途（桌宠活动气泡模板 {step}）。
      if (STATE_EVENT_TYPES.has(type)) {
      writeStateEvent(type, stepOf(event), sessionId, agentName,
        type === "turn/end" ? turnEndReasonKind(event) : "", sourceTurnOf(event));
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
      }
    } catch {}
  });

}

export { inject };
export const __retryTest = {
  threshold: RETRY_EVENT_THRESHOLD,
  reset: resetRetryConnection,
  note: noteRetryConnection,
  isModelAccess: isModelAccessError,
};
export const __hardFailureTest = {
  threshold: RETRY_EXHAUSTED_THRESHOLD,
  decideTurnEnd: decideTurnEndFailure,
  turnEndReasonKind,
  resetTurnStats,
  noteRetry: noteStatsRetry,
  recovery: noteStatsRecovery,
  noteToolResult: noteStatsToolResult,
};
export const __questionTest = {
  questionCallIdentity,
  pendingQuestionCallIds,
  registerQuestionCall,
  forgetQuestionCall,
};
// 多实例写者归属（inst 信封字段）与回合号（sourceTurn）的契约测试入口：
// 纯编码 / 纯状态事件写入 + 写队列窥视，不落盘。
export const __instanceTest = {
  bootId: BOOT_ID,
  instanceFile: INSTANCE_FILE,
  encodeRecord,
  writeRecord,
  writeStateEvent,
  sourceTurnOf,
  queued: () => writeQueue.slice(),
  resetQueue: () => { writeQueue.length = 0; },
};

// 助手正文开关（assistantText）契约测试入口：开关解析 + 正文抽取 + 开关状态，
// 纯函数/纯状态，不落盘、不起 DSH 宿主。
export const __assistantTextTest = {
  readSwitch: readAssistantTextSwitch,
  textOf: assistantTextOf,
  textMax: TEXT_MAX,
  isEnabled: () => assistantTextEnabled,
  setEnabled: (v) => { assistantTextEnabled = !!v; },
};
