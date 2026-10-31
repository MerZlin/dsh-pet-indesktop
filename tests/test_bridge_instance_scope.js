// 桥接写者归属（inst 信封字段）契约：每条记录都带本次插件启动的 UUID。
//
// 为什么必须有这个字段：多个 DSH 实例并行时各自的桥文件（dsh-{pid}.jsonl）都被
// 桌宠读入，桌宠必须能把「这条状态/交互属于哪个实例」分出来——否则 A 实例回待机
// 会撤掉 B 实例还在等的问题气泡、A 的 question/resolved 会解开 B 的锁存。
// pid 不足以承担这个身份：系统会复用 pid，同一路径也可能被轮转重建；因此插件在
// 唯一写盘出口给每条记录盖一个本次启动生成的 UUID。
//
// 这里只驱动导出的纯编码函数与写队列（不落盘、不启动 DSH 宿主）。
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

let inst;
let applyPlugin;
test.before(async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  inst = bridge.__instanceTest;
  applyPlugin = bridge.apply;
  assert.ok(inst, "bridge must export __instanceTest");
  assert.equal(typeof applyPlugin, "function", "bridge must export apply");
  inst.resetQueue();
});

test.beforeEach(() => {
  inst.resetQueue();
});

test("每条记录都带同一个非空 inst（一次启动 = 一个写者）", () => {
  const a = JSON.parse(inst.encodeRecord({ event: "tool/call", sessionId: "s1" }));
  const b = JSON.parse(inst.encodeRecord({ event: "turn/end", sessionId: "s2" }));
  assert.equal(typeof a.inst, "string");
  assert.ok(a.inst.length > 0, "inst 不得为空");
  assert.equal(a.inst, b.inst, "同一次插件启动写出的记录必须共享同一个归属");
  assert.equal(a.inst, inst.bootId, "记录上的归属就是本插件这次的 boot 标识");
});

test("inst 由插件自己盖：调用方传入同名字段被覆盖", () => {
  const rec = JSON.parse(inst.encodeRecord({ event: "tool/call", inst: "spoofed" }));
  assert.equal(rec.inst, inst.bootId, "归属不能被事件内容顶掉（顶掉即多实例隔离失效）");
  assert.notEqual(rec.inst, "spoofed");
});

test("inst 在信封顶层，且不改动既有信封字段", () => {
  const rec = JSON.parse(inst.encodeRecord({ event: "question/requested", sessionId: "s9", callId: "c1" }));
  assert.equal(rec.agent, "dsh");
  assert.equal(rec.event, "question/requested", "extra.event 仍然覆盖默认事件名");
  assert.equal(rec.sessionId, "s9");
  assert.equal(rec.callId, "c1");
  assert.ok(typeof rec.ts === "number");
  assert.ok(!("data" in rec), "inst 不得塞进嵌套载荷才有的 data 字段");
});

test("inst 不是 DSH 字段：不写进嵌套 payload", () => {
  const rec = JSON.parse(inst.encodeRecord({
    event: "cordis/request-run",
    requestId: "r1",
    payload: { requiresApproval: true },
  }));
  assert.equal(rec.inst, inst.bootId);
  assert.equal(rec.payload.inst, undefined, "嵌套载荷是上游内容，归属只属于信封");
  assert.equal(rec.payload.requiresApproval, true, "载荷原样保留");
});

test("writeRecord 真的入队带 inst 的行（接线，不只是纯函数）", () => {
  assert.equal(inst.queued().length, 0);
  inst.writeRecord({ event: "tool/call" });
  const queued = inst.queued();
  assert.equal(queued.length, 1, "每条记录进队一行");
  assert.ok(queued[0].endsWith("\n"), "每条记录一行（写盘是 append 行）");
  assert.equal(queued[0].split("\n").length, 2, "行内不得出现裸换行");
  const rec = JSON.parse(queued[0]);
  assert.equal(rec.inst, inst.bootId, "入队的行必须带归属");
  assert.equal(rec.event, "tool/call");
});

test("实例文件名带 pid，与 inst 是两个独立维度", () => {
  assert.match(inst.instanceFile, /^dsh-\d+\.jsonl$/, "文件名仍是 dsh-{pid}.jsonl");
  // 同名文件可能被同一 pid 的新实例重建：归属必须来自 inst 而不是文件名
  const again = JSON.parse(inst.encodeRecord({ event: "AgentStatus", state: "idle" }));
  assert.equal(again.inst, inst.bootId);
});

// ---- 回合号透传（sourceTurn）契约 ----
// 整封镜像信封（user/message + turn/start + turn/end 被两个写者各写一份）在桌宠侧
// （pet/dsh_state.py::TurnCompletionTracker）靠这个字段判「同一回合」——它必须**如实
// 透传** DSH 的 event.data.turn，取不到就整字段不写（旧桥/无 turn 的事件保持旧行为）。
test("sourceTurn 取自 event.data.turn，取不到给空串", () => {
  assert.equal(inst.sourceTurnOf({ data: { turn: 7 } }), "7", "回合号以字符串形态落信封");
  assert.equal(inst.sourceTurnOf({ data: { turn: "t-9" } }), "t-9");
  assert.equal(inst.sourceTurnOf({ data: { turn: 0 } }), "0", "0 是合法回合号，不得当缺失");
  assert.equal(inst.sourceTurnOf({ data: {} }), "");
  assert.equal(inst.sourceTurnOf({}), "");
  assert.equal(inst.sourceTurnOf(null), "");
  assert.equal(inst.sourceTurnOf({ data: { turn: null } }), "", "null = 没有回合号");
});

test("状态事件信封带 sourceTurn；没有回合号时整字段不写（接线）", () => {
  inst.resetQueue();
  // 与生产调用点同签名：writeStateEvent(type, step, sessionId, agentName, reasonKind, sourceTurn)
  inst.writeStateEvent("turn/end", "turn:7", "s1", "", "", "7");
  inst.writeStateEvent("turn/end", "turn:7", "s1");
  const queued = inst.queued();
  assert.equal(queued.length, 2, "每条状态事件进队一行");
  const withTurn = JSON.parse(queued[0]);
  const withoutTurn = JSON.parse(queued[1]);
  assert.equal(withTurn.sourceTurn, "7");
  assert.equal(withTurn.event, "turn/end");
  assert.equal(withTurn.step, "turn:7");
  assert.equal(withTurn.inst, inst.bootId, "回合号不改动归属信封");
  assert.ok(!("sourceTurn" in withoutTurn), "无回合号的记录不得写出空 sourceTurn");
  inst.resetQueue();   // 清空入队记录：迟到的合批定时器不得把测试记录写进真桥目录
});

test("session/event 真处理函数：整封转发的记录都带回合号，无 turn 时不带", () => {
  // 只驱动真 apply 注册的 session/event 处理函数 + 写队列窥视（不落盘、不起 DSH 宿主）
  const handlers = new Map();
  applyPlugin({ on: (name, fn) => {
    const list = handlers.get(name) || [];
    list.push(fn);
    handlers.set(name, list);
  } });
  const onSessionEvent = handlers.get("session/event")?.[0];
  assert.ok(typeof onSessionEvent === "function", "apply 必须注册 session/event 处理函数");

  const session = { id: "s-mirror" };
  inst.resetQueue();
  const envelope = {
    "user/message": { type: "user/message", data: { turn: 7, step: 3, source: { kind: "user" } } },
    "turn/start": { type: "turn/start", data: { turn: 7, step: 3 } },
    "turn/end": { type: "turn/end", data: { turn: 7, step: 3, reason: { kind: "completed" } } },
  };
  for (const event of Object.values(envelope)) onSessionEvent(session, event);
  // 旧桥/无 turn 的事件：整字段不写（桌宠侧退回原判据）
  onSessionEvent(session, { type: "user/message", data: { step: 3, source: { kind: "user" } } });
  const records = inst.queued().map((line) => JSON.parse(line));
  inst.resetQueue();

  const pick = (event) => records.filter((rec) => rec.event === event);
  assert.equal(pick("user/message").length, 2, "两条真人消息各写一行");
  assert.equal(pick("user/message")[0].sourceTurn, "7", "镜像信封的人消息也要带回合号");
  assert.ok(!("sourceTurn" in pick("user/message")[1]), "无 data.turn 的记录不得写出空字段");
  assert.equal(pick("turn/start")[0].sourceTurn, "7");
  assert.equal(pick("turn/end")[0].sourceTurn, "7");
  assert.equal(pick("turn/end")[0].reasonKind, "completed", "回合号不挤掉既有字段");
});
