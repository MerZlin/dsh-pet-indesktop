// 真机回归（2026-10-31）：DSH 在桌宠点同意后的续跑里会改写工具调用的
// description（"Run user-supplied…" → "Retry approved…"）。旧实现把整段
// 参数 JSON（含 description）算进审批指纹，一次性通行证永远匹配不上，
// 每次重试都再弹一张审批卡——桌宠侧看起来就是「等待审批卡死」。
import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalFingerprint,
  isInternalUser,
} from "../integrations/dsh-pet-bridge/channel.js";

const exec = (args) => ({
  name: "pwsh",
  arguments: JSON.stringify(args),
});

test("重试时只改说明字段，通行证指纹不变", () => {
  const first = exec({
    description: "Run user-supplied Remove-Item command verbatim",
    command: "Remove-Item -LiteralPath E:/tmp/a.txt -Force",
  });
  const retry = exec({
    description: "Retry approved Remove-Item command verbatim",
    command: "Remove-Item -LiteralPath E:/tmp/a.txt -Force",
  });
  assert.equal(approvalFingerprint(first), approvalFingerprint(retry));
});

test("命令本身变化必须换指纹", () => {
  const safe = exec({ description: "x", command: "Remove-Item E:/tmp/a.txt" });
  const other = exec({ description: "x", command: "Remove-Item E:/tmp/b.txt" });
  assert.notEqual(approvalFingerprint(safe), approvalFingerprint(other));
});

test("参数键顺序不影响指纹", () => {
  const a = exec({ command: "Remove-Item E:/tmp/a.txt", description: "x" });
  const b = {
    name: "pwsh",
    arguments: JSON.stringify({ description: "x", command: "Remove-Item E:/tmp/a.txt" }),
  };
  assert.equal(approvalFingerprint(a), approvalFingerprint(b));
});

test("DSH 注入的上下文消息不算真人发言", () => {
  for (const kind of ["runtime-context", "skill-catalog", "time-context", "plugin"]) {
    assert.equal(isInternalUser({ data: { source: { kind } } }), true, kind);
  }
  assert.equal(isInternalUser({ data: { source: { kind: "user" } } }), false);
  assert.equal(isInternalUser({ data: {} }), false);
});
