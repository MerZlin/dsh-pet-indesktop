// dsh-pet 桌宠桥接插件（零依赖，仅写本地文件，无网络请求）
// 订阅 DSH 的 agent 生命周期事件，追加写入共享桥目录的 dsh.jsonl，
// 桌宠侧的 DshMonitor 通过 byte-offset tail 读取（不回放历史）。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）
const PLUGIN_ID = "dsh-pet-bridge";

// 进程内状态去重 + 多 Agent 聚合：
// 1) dsh 在 agent 创建/状态切换瞬间会抖动出重复 idle（实测 idle→working 仅隔
//    4ms），重复聚合状态不落盘——否则桌宠端 2 秒换帧节流会吞掉真实 working。
// 2) 必须按 agent 分别跟踪再聚合（任一在忙 = 忙）：dsh 可并发多个 agent
//   （子代理/多会话），全局单值去重会让先完成的 agent 把还在干活的顶成 idle。
const agentStates = new Map(); // agent 对象 → "working" | "idle"
let lastState = null;

function aggregateWrite() {
  const anyBusy = [...agentStates.values()].some((s) => s === "working");
  const next = anyBusy ? "working" : "idle";
  if (next === lastState) return;
  lastState = next;
  writeRecord({ state: next });
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
function writeTool(tool) {
  writeRecord({ event: "tool/call", tool });
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

export function apply(ctx) {
  // 依赖 cordis 的 context 生命周期：agent/status 监听挂在 agent.ctx 上，
  // agent 销毁时随其 context 自动解绑，不累积 disposer。
  ctx.on("agent/created", ({ agent }) => {
    if (!agent) return;
    // 注意：创建时不要写 idle——桌宠端本来就默认 idle 态。
    // 实测 dsh 创建 agent 后 4ms 内必发 running，此时若先写一条幻影 idle，
    // 会占住桌宠端 2 秒换帧节流位，把紧跟的真实 working 整个吞掉。
    agent.ctx.effect(() => {
      agentStates.set(agent, "idle");
      const stop = agent.ctx.on("agent/status", ({ status }) => {
        agentStates.set(agent, status === "running" ? "working" : "idle");
        aggregateWrite();
      });
      return () => {
        if (typeof stop === "function") stop();
        // agent 销毁：移出聚合并重算（全部退出时落一条 idle，桌宠回待机）
        agentStates.delete(agent);
        aggregateWrite();
      };
    }, `${PLUGIN_ID}.agent()`);
  });

  // 过程汇报：session/event 在插件/根/agent 三层上下文都可达（实测验证）。
  // 注意 dsh 的工具调用不走独立 tool/call 事件——工具名在 assistant/message
  // 事件的 content 块里（type === "tool-call" 的块带 name 字段），
  // web UI 的工具卡片也是这么来的。assistant/message 每步只发一次，无流式重复。
  ctx.on("session/event", (_session, event) => {
    try {
      if (!event || event.type !== "assistant/message") return;
      // data 形状：{ turn, step, message: { content: [...] } }（兼容 data 直接是消息）
      const data = event.data || {};
      const content = (data.message && data.message.content) || data.content;
      if (!Array.isArray(content)) return;
      for (const block of content) {
        if (block && block.type === "tool-call" && block.name) {
          writeTool(String(block.name));
        }
      }
    } catch {}
  });
}
