// DSH 桌面端在助手消息上显示「已思考 N 字」。桥的 SSE 帧以前只带正文，
// 桌宠拿不到思维链就没法对齐这条信息。assistant/message 帧必须同时带
// reasoning（多块用空行连接，超长按 8000 截断），且不影响其他帧的既有映射。
import assert from "node:assert/strict";
import test from "node:test";

import { frameOf } from "../integrations/dsh-pet-bridge/channel.js";

const assistant = (content, extra = {}) => ({
  type: "assistant/message",
  data: { turn: 1, step: 2, message: { content }, ...extra },
});

test("assistant 帧带 reasoning，正文不变", () => {
  const frame = frameOf(
    assistant([{ type: "reasoning", text: "先看看文件" }, { type: "text", text: "好了" }]),
  );
  assert.equal(frame.kind, "assistant");
  assert.equal(frame.text, "好了");
  assert.equal(frame.reasoning, "先看看文件");
  assert.equal(frame.turn, 1);
  assert.equal(frame.step, 2);
});

test("只有正文时 reasoning 是空串", () => {
  const frame = frameOf(assistant([{ type: "text", text: "好了" }]));
  assert.equal(frame.reasoning, "");
});

test("thinking 块同样算思维链（DSH 两种块名都出现过）", () => {
  const frame = frameOf(
    assistant([{ type: "thinking", text: "想想" }, { type: "text", text: "嗯" }]),
  );
  assert.equal(frame.reasoning, "想想");
});

test("多条 reasoning 用空行连接", () => {
  const frame = frameOf(
    assistant([
      { type: "reasoning", text: "第一步" },
      { type: "text", text: "中间" },
      { type: "reasoning", text: "第二步" },
    ]),
  );
  assert.equal(frame.reasoning, "第一步\n\n第二步");
  assert.equal(frame.text, "中间");
});

test("空的 reasoning 块不产生多余空行", () => {
  const frame = frameOf(
    assistant([
      { type: "reasoning", text: "" },
      { type: "reasoning", text: "有内容" },
      { type: "text", text: "好" },
    ]),
  );
  assert.equal(frame.reasoning, "有内容");
});

test("reasoning 超长截断到 8000 字并加省略号", () => {
  const long = "思".repeat(8001);
  const frame = frameOf(assistant([{ type: "reasoning", text: long }]));
  assert.equal(frame.reasoning.length, 8001);
  assert.equal(frame.reasoning, `${"思".repeat(8000)}…`);
});

test("既有帧映射不受影响", () => {
  assert.deepEqual(
    frameOf({
      type: "tool/call",
      data: { turn: 1, step: 1, callId: "c1", name: "pwsh", arguments: '{"command":"ls"}' },
    }),
    { kind: "tool-call", turn: 1, step: 1, callId: "c1", tool: "pwsh", args: '{"command":"ls"}' },
  );
  const end = frameOf({ type: "turn/end", data: { turn: 1, reason: { kind: "completed" } } });
  assert.equal(end.kind, "turn-end");
  assert.equal(end.reason, "completed");
  assert.equal(frameOf({ type: "unknown/event", data: {} }), null);
});
