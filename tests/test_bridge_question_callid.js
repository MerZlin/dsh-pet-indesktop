// F2b 回归：mux 的 question/requested、question/resolved 帧必须补上 callId。
//
// 背景：mux 帧只带 rpcId，而 callId 是 tool/call 兜底路径的登记身份（桥接内
// 复合键 sessionId|callId 存在 pendingQuestionCallIds）。桌宠端问题气泡在
// 升级重建后靠 callId 与兜底 resolved 配对；帧里缺 callId 时，mux 断线场景下
// 兜底发出的 question/resolved(callId) 就匹配不到，气泡永远关不掉。
// 测试直接驱动与生产 mux 分支相同的构造函数，不启动 DSH 宿主与 WebSocket。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

let q;
test.before(async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  q = bridge.__questionTest;
  assert.ok(q, "bridge must export __questionTest");
});

test.beforeEach(() => {
  q.pendingQuestionCallIds.clear();
  q.pendingQuestionRpcPairs.clear();
  q.pendingQuestionOrder.clear();
});

test("mux question/requested 帧按会话 FIFO 补写 callId", () => {
  q.registerQuestionCall("call-9", "sess-1");
  const rec = q.muxQuestionRequestedRecord("rpc-7", {
    sessionId: "sess-1",
    questions: [{ id: "q1", question: "选择？" }],
  });
  assert.equal(rec.event, "question/requested");
  assert.equal(rec.rpcId, "rpc-7");
  assert.equal(rec.sessionId, "sess-1");
  assert.equal(
    rec.callId,
    "call-9",
    "帧必须带上同会话待答的 callId，否则 mux 断线时兜底 resolved 关不掉气泡",
  );
});

test("mux question/resolved 帧同样补写 callId", () => {
  q.registerQuestionCall("call-9", "sess-1");
  q.muxQuestionRequestedRecord("rpc-7", { sessionId: "sess-1", questions: [] });
  const rec = q.muxQuestionResolvedRecord("rpc-7", { sessionId: "sess-1", outcome: "answered" });
  assert.equal(rec.event, "question/resolved");
  assert.equal(rec.rpcId, "rpc-7");
  assert.equal(rec.callId, "call-9");
});

test("反查限定同一会话，不把别的会话的 callId 串到本帧", () => {
  q.registerQuestionCall("call-9", "sess-1");
  const rec = q.muxQuestionRequestedRecord("rpc-8", { sessionId: "sess-2", questions: [] });
  assert.equal(rec.callId, "", "不得跨会话挂 callId");
});

test("同会话多问题按 FIFO 配对：每帧拿到自己那份 callId", () => {
  q.registerQuestionCall("call-a", "sess-1");
  q.registerQuestionCall("call-b", "sess-1");
  const recA = q.muxQuestionRequestedRecord("rpc-a", { sessionId: "sess-1", questions: [] });
  const recB = q.muxQuestionRequestedRecord("rpc-b", { sessionId: "sess-1", questions: [] });
  assert.equal(recA.callId, "call-a");
  assert.equal(recB.callId, "call-b", "第二个帧不得再拿到最旧的 call-a（C3）");
  const resA = q.muxQuestionResolvedRecord("rpc-a", { sessionId: "sess-1", outcome: "answered" });
  const resB = q.muxQuestionResolvedRecord("rpc-b", { sessionId: "sess-1", outcome: "answered" });
  assert.equal(resA.callId, "call-a");
  assert.equal(resB.callId, "call-b", "resolved 帧按 rpcId 取回各自的 callId");
});

test("resolved 取回配对后即清理；forget 同步清出队条目", () => {
  q.registerQuestionCall("call-a", "sess-1");
  q.muxQuestionRequestedRecord("rpc-a", { sessionId: "sess-1", questions: [] });
  q.muxQuestionResolvedRecord("rpc-a", { sessionId: "sess-1", outcome: "answered" });
  assert.equal(q.pendingQuestionRpcPairs.size, 0, "resolved 是终态，配对取回即清");
  q.registerQuestionCall("call-c", "sess-1");
  q.forgetQuestionCall("call-c", "sess-1");
  const queue = q.pendingQuestionOrder.get("sess-1") || [];
  assert.ok(!queue.includes("call-c"), "resolveQuestion 经 forget 同步清出队条目");
});

test("帧自带 callId 时原样保留", () => {
  const rec = q.muxQuestionRequestedRecord("rpc-7", {
    sessionId: "sess-1",
    callId: "call-frame",
    questions: [],
  });
  assert.equal(rec.callId, "call-frame");
});

test("mux 分支确实走构造函数（防还原成内联写盘后本文件仍绿）", () => {
  // 桥接已拆为壳 + impl/<version>/：mux relay 段住在实现层，源码检查须读 impl。
  const pkg = JSON.parse(fs.readFileSync(path.join(bridgeDir, "package.json"), "utf8"));
  const src = fs.readFileSync(path.join(bridgeDir, "impl", String(pkg.version || ""), "index.js"), "utf8");
  const muxBranch = src.split("// ===== interactive mux relay =====")[1] || "";
  assert.ok(
    muxBranch.includes("muxQuestionRequestedRecord(") && muxBranch.includes("muxQuestionResolvedRecord("),
    "mux relay 段必须调用 callId 构造函数；改回内联 writeRecord 会让 F2b 修复静默失效",
  );
});
