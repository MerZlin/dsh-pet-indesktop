// P1-4：审批/问题写盘去重的降级键必须带 sessionId。
//
// 背景：`ap:tc:<tool>|<command>` 不含 session/审批身份，8 秒内两个不同审批
// （同命令）会被静默丢弃。本测试直接读取并求值 index.js 里的纯函数
// `_interactionDedupKeys`，避免拉起 DSH 运行时依赖（@deepseek-ai/* 未安装
// 时整份模块无法 import）。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(here, "../integrations/dsh-pet-bridge/index.js");
const source = fs.readFileSync(sourcePath, "utf8");

function loadInteractionDedupKeys() {
  const match = source.match(/function _interactionDedupKeys\(extra\) \{[\s\S]*?\n\}/);
  assert.ok(match, "index.js 应定义 _interactionDedupKeys");
  return new Function(`${match[0]}; return _interactionDedupKeys;`)();
}

test("approval fallback dedup key distinguishes sessions", () => {
  const keysFor = loadInteractionDedupKeys();
  const base = { event: "approval/request", tool: "exec_command", command: "rm -rf /tmp/x" };

  const keysA = keysFor({ ...base, sessionId: "sess-a" });
  const keysB = keysFor({ ...base, sessionId: "sess-b" });
  const fallbackA = keysA.find((key) => key.startsWith("ap:tc:"));
  const fallbackB = keysB.find((key) => key.startsWith("ap:tc:"));

  assert.ok(fallbackA, "应生成 tool+command 降级去重键");
  assert.ok(fallbackB, "应生成 tool+command 降级去重键");
  assert.notEqual(
    fallbackA,
    fallbackB,
    "不同 session 的同命令审批不得共用降级去重键（会被 8s 窗口静默丢弃）",
  );
  // 同一 session 的同一命令仍应得到同一组键（去重语义不变）。
  assert.deepEqual(keysFor({ ...base, sessionId: "sess-a" }), keysA);
});

test("approval fallback dedup key without sessionId stays legacy", () => {
  const keysFor = loadInteractionDedupKeys();
  const keys = keysFor({ event: "approval/request", tool: "exec", command: "ls" });
  assert.deepEqual(keys, ["ap:tc:exec|ls"]);
});

test("approval identity key still wins when approvalId is present", () => {
  const keysFor = loadInteractionDedupKeys();
  const keys = keysFor({
    event: "approval/request", approvalId: "ap-1", sessionId: "sess-a",
    tool: "exec", command: "ls",
  });
  assert.ok(keys.includes("ap:ap-1"));
  assert.ok(keys.includes("ap:se:sess-a:ap-1"));
});

test("distinct approvals in one session never share a dedup key", () => {
  // 审计场景 1：用户拒绝 pwsh Get-Location，Agent 8s 内再次请求同一条命令
  // → 第二条 approval/request 被粗降级键 ap:tc:<tool>|<command> 丢弃。
  const keysFor = loadInteractionDedupKeys();
  const base = {
    event: "approval/request", sessionId: "sess-a",
    tool: "pwsh", command: "Get-Location",
  };
  const first = keysFor({ ...base, approvalId: "ap-1", rpcId: "rpc-1" });
  const second = keysFor({ ...base, approvalId: "ap-2", rpcId: "rpc-2" });
  const shared = first.filter((key) => second.includes(key));
  assert.deepEqual(
    shared,
    [],
    "同一会话内两条身份不同的审批（同命令）不得共享任何去重键",
  );
});
