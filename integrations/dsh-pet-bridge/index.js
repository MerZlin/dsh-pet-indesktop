// dsh-pet 桌宠桥接插件（零依赖，仅写本地文件，无网络请求）
// 订阅 DSH 的会话事件流，归约出桌宠状态（idle/thinking/working/attention/error），
// 追加写入共享桥目录的 dsh.jsonl，桌宠侧的 DshMonitor 通过 byte-offset tail 读取。
//
// 事件归约参考 QCYTSN/dsh-dafeiyu 的 companion-reducer（已核对 dsh-agent-loop
// 源码确认事件与数据形状）：turn/start→思考、tool/call→工作、approval/asked→
// 等待确认、turn/end→完成/错误。状态聚合优先级（多会话并发时取最需要注意的）：
//   attention > error > working > thinking > idle
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）

// 桌宠端状态词汇（与桌宠 agent_link.py 的 VALID_STATES 对齐）
const PRIORITY = { idle: 0, thinking: 20, working: 30, error: 50, attention: 60 };

// 会向用户提问/请求批准的工具：调用期间视为「等待确认」。
// 只认确切工具名，不用模糊子串（避免把 review/allowlist 之类误判成等待）。
const USER_QUESTION_TOOLS = new Set(["ask_user_question", "exit_plan_mode"]);

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

function writeRecord(extra) {
  try {
    const dir = bridgeDir();
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, "dsh.jsonl");
    try {
      // 超上限轮转：dsh.jsonl → dsh.jsonl.1（只留一代）
      if (fs.existsSync(file) && fs.statSync(file).size > MAX_BYTES) {
        // Windows 不允许 rename 覆盖已存在目标，先删再转
        fs.rmSync(file + ".1", { force: true });
        fs.renameSync(file, file + ".1");
      }
    } catch {}
    fs.appendFileSync(
      file,
      JSON.stringify({ ts: Date.now() / 1000, agent: "dsh", event: "AgentStatus", ...extra }) + "\n",
      "utf8",
    );
  } catch {
    // 静默失败：桥接是锦上添花，绝不能影响 DSH 本体
  }
}

// 过程汇报：工具调用事件（state 不变，只带 tool 字段，桌宠端据此弹「正在跑命令…」）
function writeTool(tool) {
  writeRecord({ event: "tool/call", tool });
}

// ----------------------------------------------------------------------
// 会话状态归约（每会话一份记录，聚合后只在变化时落盘）
// ----------------------------------------------------------------------
// record: { state, openTools: Set<callId>, waiting: bool }
const sessions = new Map();
let lastAgg = null;
// 旧版宿主兼容：agent/status 只有 running/idle。一旦见过富事件（turn/start 等）
// 就停用该回退通道——否则 running→working 会把「思考」覆盖成「工作」。
let sawRichEvents = false;

function sessionIdOf(session, event) {
  return String(
    session?.header?.id ?? session?.id ?? event?.data?.sessionId ?? "unknown-session",
  );
}

function isSubagent(session) {
  return session?.header?.origin === "subagent"
    || Number(session?.header?.delegationDepth ?? 0) > 0;
}

function aggregate() {
  let best = "idle";
  let bestP = -1;
  for (const r of sessions.values()) {
    const p = PRIORITY[r.state] ?? 0;
    if (p > bestP) {
      bestP = p;
      best = r.state;
    }
  }
  if (best === lastAgg) return;
  lastAgg = best;
  writeRecord({ state: best });
}

function update(record, state) {
  if (record.state === state) {
    aggregate(); // 其他会话可能刚退出，聚合并不能假设不变（内部有去重，代价可忽略）
    return;
  }
  record.state = state;
  aggregate();
}

function resumeState(record) {
  return record.openTools.size > 0 ? "working" : "thinking";
}

// 兼容旧宿主的工具名提取：assistant/message 的 content 块里 type==="tool-call" 的块
function toolsFromAssistantMessage(event) {
  const data = event?.data || {};
  const content = (data.message && data.message.content) || data.content;
  if (!Array.isArray(content)) return [];
  const names = [];
  for (const block of content) {
    if (block && block.type === "tool-call" && block.name) names.push(String(block.name));
  }
  return names;
}

function handleEvent(session, event) {
  if (!event || typeof event.type !== "string") return;
  if (isSubagent(session)) return; // 子代理状态不抢桌宠（刷屏且无信息量）

  const id = sessionIdOf(session, event);
  let r = sessions.get(id);
  if (!r) {
    r = { state: "idle", openTools: new Set(), waiting: false };
    sessions.set(id, r);
  }

  switch (event.type) {
    case "turn/start":
      r.openTools.clear();
      r.waiting = false;
      update(r, "thinking");
      break;

    case "step/start":
    case "assistant/chunk":
      if (!r.waiting && r.openTools.size === 0) update(r, "thinking");
      break;

    case "assistant/message":
      // 旧宿主没有独立 tool/call 事件，工具名从消息内容块兜底提取
      for (const name of toolsFromAssistantMessage(event)) writeTool(name);
      if (!r.waiting && r.openTools.size === 0) update(r, "thinking");
      break;

    case "tool/call": {
      // dsh-agent-loop: session.append("tool/call", { turn, step, callId, name, arguments })
      const name = String(event.data?.name ?? event.data?.message?.name ?? "");
      const callId = String(
        event.data?.callId ?? event.data?.message?.source?.callId ?? `seq-${event.seq ?? "?"}`,
      );
      r.openTools.add(callId);
      if (name) writeTool(name);
      if (USER_QUESTION_TOOLS.has(name.toLowerCase())) {
        r.waiting = true;
        update(r, "attention");
      } else {
        update(r, "working");
      }
      break;
    }

    case "tool/result": {
      const callId = String(
        event.data?.callId ?? event.data?.message?.source?.callId ?? "",
      );
      if (callId) r.openTools.delete(callId);
      // 工具失败不切 error（grep 没结果之类太常见，会刷错误气泡）；
      // 真正的失败由 turn/end 的 reason.kind 上报。
      if (r.waiting && !r.openTools.size) {
        // 等待中的提问工具已返回但用户消息未到：保守保持等待
        break;
      }
      if (!r.waiting) update(r, resumeState(r));
      break;
    }

    case "approval/asked":
      r.waiting = true;
      update(r, "attention");
      break;

    case "approval/decided":
      r.waiting = false;
      update(r, resumeState(r));
      break;

    case "user/message":
      if (r.waiting) {
        r.waiting = false;
        update(r, resumeState(r));
      }
      break;

    case "turn/end": {
      const kind = String(event.data?.reason?.kind ?? "completed");
      r.openTools.clear();
      r.waiting = false;
      if (kind === "blocked") update(r, "attention");
      else if (kind === "completed" || kind === "aborted") update(r, "idle");
      else update(r, "error"); // max-tokens / error 等异常结束
      break;
    }

    default:
      // 未知事件不产生状态：直接返回、不置 sawRichEvents。否则混合协议
      // 序列「未知 rich event → 旧版 agent/status: running」会永久跳过
      // legacy 回退通道，桌宠卡在 idle（P2 修复：只有能产生状态的已知
      // rich event 才停用旧版宿主回退）。
      return;
  }
  // 走到这里 = 命中了能产生状态的已知 rich event：旧版宿主回退通道
  // 的数据太粗（running/idle），会覆盖 thinking/working 等细粒度状态，
  // 从此停用。未知事件不经过此处（上面 default 已 return）。
  sawRichEvents = true;
}

function disposeSession(session) {
  if (sessions.delete(sessionIdOf(session))) aggregate();
}

export function apply(ctx) {
  // 富事件通道：挂根总线（{ global: true }）——loader 条目可能位于作用域组合内，
  // 不挂根总线会漏掉其他 scope 的会话事件（同 dsh-dafeiyu 的实测结论）。
  // 绝不能让本插件的异常逃进共享会话总线：会连带打断其他插件的事件订阅。
  ctx.on("session/event", (session, event) => {
    try {
      handleEvent(session, event);
    } catch {}
  }, { global: true });
  ctx.on("session/disposed", (session) => {
    try {
      disposeSession(session);
    } catch {}
  }, { global: true });

  // 旧版宿主回退通道：agent/status 只有 running/idle，仅在没见过富事件时生效。
  // 多 Agent 聚合（任一在忙=忙）：dsh 可并发多个 agent（子代理/多会话），
  // 全局单值去重会让先完成的 agent 把还在干活的顶成 idle。
  const agentStates = new Map(); // agent 对象 → "working" | "idle"
  let lastLegacy = null;
  const legacyAggregate = () => {
    if (sawRichEvents) return;
    const anyBusy = [...agentStates.values()].some((s) => s === "working");
    const next = anyBusy ? "working" : "idle";
    if (next === lastLegacy) return;
    lastLegacy = next;
    writeRecord({ state: next });
  };
  ctx.on("agent/created", ({ agent }) => {
    if (!agent) return;
    // 创建时不写 idle：桌宠端本来就默认 idle 态，且实测 dsh 创建 agent 后
    // 4ms 内必发 running，幻影 idle 会占住桌宠端换帧节流位、吞掉真实 working。
    agent.ctx.effect(() => {
      agentStates.set(agent, "idle");
      const stop = agent.ctx.on("agent/status", ({ status }) => {
        agentStates.set(agent, status === "running" ? "working" : "idle");
        legacyAggregate();
      });
      return () => {
        if (typeof stop === "function") stop();
        agentStates.delete(agent);
        legacyAggregate();
      };
    }, "dsh-pet-bridge.agent()");
  });
}
