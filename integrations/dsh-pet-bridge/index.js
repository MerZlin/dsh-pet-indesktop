// dsh-pet 桌宠桥接插件（零依赖，仅写本地文件，无网络请求）
// 订阅 DSH 的 agent 生命周期事件，追加写入共享桥目录的 dsh.jsonl，
// 桌宠侧的 DshMonitor 通过 byte-offset tail 读取（不回放历史）。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MAX_BYTES = 1024 * 1024; // 事件文件超过 1MB 时轮转（保留 .1 备份，防无限增长）
const PLUGIN_ID = "dsh-pet-bridge";

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

function writeEvent(state) {
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
      JSON.stringify({ ts: Date.now() / 1000, agent: "dsh", event: "AgentStatus", state }) + "\n",
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
    writeEvent("idle");
    agent.ctx.effect(() => {
      const stop = agent.ctx.on("agent/status", ({ status }) => {
        writeEvent(status === "running" ? "working" : "idle");
      });
      return () => {
        if (typeof stop === "function") stop();
      };
    }, `${PLUGIN_ID}.agent()`);
  });
}
