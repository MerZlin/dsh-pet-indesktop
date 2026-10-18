// 助手正文开关（assistantText）契约：默认关 = #226 脱敏逐字节不变；开 = 补写 text。
//
// 背景：#226 减法把 assistant/message 的模型回复正文从落盘记录里拿掉了，桌宠/手机端
// 于是拿不到正文，只能退回预设短句。docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md
// 的事件表本来就把 assistant/message 的字段写成 text + sessionId，桌宠侧
// pet/dsh_state.py:808 也按 record["text"] 读取——所以本开关是把契约里既有的字段
// 按需放回来，不是新造字段。
//
// 两件必须守住的事：
//   1) 默认（不传 config / config 里没有 assistantText）绝不写 text——脱敏是安全侧默认；
//   2) 开的时候只放行**助手回复正文**（type === "text" 块），同数组里的 reasoning
//      思考块与 tool-call 参数块仍然不落盘。
//
// 这里只驱动导出的纯函数与写队列（不落盘、不起 DSH 宿主）。
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

let at;
let inst;
let applyPlugin;
test.before(async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  at = bridge.__assistantTextTest;
  inst = bridge.__instanceTest;
  applyPlugin = bridge.apply;
  assert.ok(at, "bridge must export __assistantTextTest");
  assert.ok(inst, "bridge must export __instanceTest");
  assert.equal(typeof applyPlugin, "function", "bridge must export apply");
});

test.afterEach(() => {
  // 开关是模块级状态：每个用例后复位，避免用例间互相影响
  at.setEnabled(false);
  inst.resetQueue();
});

// ---------------------------------------------------------------- 1. 开关解析
test("未配置时开关为关（脱敏是安全侧默认）", () => {
  assert.equal(at.readSwitch(undefined), false, "不传 config 必须为关");
  assert.equal(at.readSwitch({}), false, "config 里没有 assistantText 必须为关");
  assert.equal(at.readSwitch({ assistantText: false }), false);
  assert.equal(at.readSwitch({ assistantText: null }), false);
  assert.equal(at.readSwitch({ assistantText: undefined }), false);
});

test("只认显式真值，认不出一律为关", () => {
  assert.equal(at.readSwitch({ assistantText: true }), true, "布尔 true 为开");
  for (const raw of ["1", "true", "TRUE", " yes ", "on", "On"]) {
    assert.equal(at.readSwitch({ assistantText: raw }), true, `字符串真值 ${JSON.stringify(raw)} 为开`);
  }
  for (const raw of ["0", "false", "off", "no", "", "  ", "maybe", 2, {}]) {
    assert.equal(at.readSwitch({ assistantText: raw }), false, `认不出的值 ${JSON.stringify(raw)} 必须为关`);
  }
});

test("模块级默认是关：import 后未调 apply 时开关状态为 false", () => {
  assert.equal(at.isEnabled(), false, "未经 apply 配置时不得处于开启态");
});

// ---------------------------------------------------------------- 2. 正文抽取
test("从 content 的 text 块取正文，忽略 reasoning 与 tool-call 块", () => {
  // 真实形状（对 DSH 0.2.0-rc.2 会话存档实测）：data.message.content 是块数组，
  // 回复正文在 type === "text" 的块里，同数组混着 reasoning / tool-call。
  const event = {
    data: {
      turn: 3,
      step: 40,
      message: {
        role: "assistant",
        content: [
          { type: "reasoning", text: "这段是思考，绝不能当正文" },
          { type: "text", text: "电脑上干完活啦" },
          { type: "tool-call", id: "c1", name: "bash", arguments: { command: "secret-cmd" } },
        ],
      },
    },
  };
  const text = at.textOf(event);
  assert.equal(text, "电脑上干完活啦");
  assert.ok(!text.includes("思考"), "思考块不得混进正文");
  assert.ok(!text.includes("secret-cmd"), "工具参数不得混进正文");
});

test("多个 text 块按顺序拼接", () => {
  const event = {
    data: {
      message: {
        content: [
          { type: "text", text: "第一段" },
          { type: "reasoning", text: "middle" },
          { type: "text", text: "第二段" },
        ],
      },
    },
  };
  assert.equal(at.textOf(event), "第一段\n第二段");
});

test("兼容旧形状：data.text / event.text 作为兜底", () => {
  assert.equal(at.textOf({ data: { text: "顶层 data.text" } }), "顶层 data.text");
  assert.equal(at.textOf({ text: "事件根 text" }), "事件根 text");
  // content 里有 text 块时优先用它，不被兜底覆盖
  assert.equal(
    at.textOf({ data: { text: "旧的", message: { content: [{ type: "text", text: "新的" }] } } }),
    "新的",
  );
});

test("取不到正文时返回空串，不抛异常", () => {
  for (const bad of [null, undefined, {}, { data: {} }, { data: { message: {} } },
    { data: { content: [] } }, { data: { message: { content: "不是数组" } } }]) {
    assert.equal(at.textOf(bad), "", `畸形输入 ${JSON.stringify(bad)} 应得空串`);
  }
});

test("只有非 text 块时不得把它们的 text 字段当正文", () => {
  const event = {
    data: { message: { content: [{ type: "reasoning", text: "只有思考" }] } },
  };
  assert.equal(at.textOf(event), "", "仅 reasoning 块时必须为空（不能兜底成思考内容）");
});

// ---------------------------------------------------------------- 3. 落盘行为
test("开关关闭：assistant/message 记录不带 text（#226 行为）", () => {
  at.setEnabled(false);
  inst.writeStateEvent("assistant/message", "turn:3", "s1", "", "", "3", "");
  const record = JSON.parse(inst.queued()[0]);
  assert.ok(!("text" in record), "关开关时记录里绝不能出现 text 字段");
  assert.equal(record.event, "assistant/message");
  assert.equal(record.step, "turn:3");
  assert.equal(record.sessionId, "s1");
  assert.equal(record.sourceTurn, "3");
});

test("开关打开：assistant/message 记录带 text，且其余字段不变", () => {
  at.setEnabled(true);
  const body = at.textOf({
    data: { message: { content: [{ type: "text", text: "电脑上干完活啦" }] } },
  });
  inst.writeStateEvent("assistant/message", "turn:3", "s1", "", "", "3", body);
  const record = JSON.parse(inst.queued()[0]);
  assert.equal(record.text, "电脑上干完活啦");
  assert.equal(record.event, "assistant/message");
  assert.equal(record.step, "turn:3");
  assert.equal(record.sessionId, "s1");
  assert.equal(record.sourceTurn, "3");
  assert.equal(record.inst, inst.bootId, "正文开关不改动归属信封");
});

test("空正文即使开着开关也不写出空 text 字段", () => {
  at.setEnabled(true);
  inst.writeStateEvent("assistant/message", "turn:3", "s1", "", "", "3", "");
  const record = JSON.parse(inst.queued()[0]);
  assert.ok(!("text" in record), "空正文不得写出空 text");
});

test("正文按 TEXT_MAX 截断", () => {
  assert.equal(at.textMax, 300, "截断上限沿用 TEXT_MAX=300");
  const long = "字".repeat(at.textMax + 120);
  const capped = long.length > at.textMax ? long.slice(0, at.textMax) : long;
  assert.equal(capped.length, at.textMax);
  inst.writeStateEvent("assistant/message", "turn:3", "s1", "", "", "3", capped);
  const record = JSON.parse(inst.queued()[0]);
  assert.equal(record.text.length, at.textMax, "落盘正文不得超过 TEXT_MAX");
});

// ---------------------------------------------------------------- 4. 端到端接线
// 上面的用例单独验解析/抽取/写入三块；这里必须再验一次**真 apply 接线**——
// 否则「config 传进去了却没读到」这类断线会漏网（开关永远为关 = 用户白配置）。
// 只驱动真 apply 注册的 session/event 处理函数 + 写队列窥视（不落盘、不起 DSH 宿主）。
// 返回该事件写出的**全部**记录（assistant/message 与 tool/call 都要看得见），
// 由各用例自行挑；只取 assistant/message 会让脱敏回归闸验不到工具记录。
function collectAllRecords(applyConfig, event) {
  const handlers = new Map();
  applyPlugin({ on: (name, fn) => { handlers.set(name, fn); } }, applyConfig);
  const onSessionEvent = handlers.get("session/event");
  assert.ok(typeof onSessionEvent === "function", "apply 必须注册 session/event 处理函数");
  inst.resetQueue();
  onSessionEvent({ id: "s-e2e" }, event);
  const records = inst.queued().map((line) => JSON.parse(line));
  inst.resetQueue();
  return records;
}

const collectAssistantRecords = (applyConfig, event) =>
  collectAllRecords(applyConfig, event).filter((rec) => rec.event === "assistant/message");

const REAL_EVENT = {
  type: "assistant/message",
  data: {
    turn: 3,
    step: 40,
    message: {
      role: "assistant",
      content: [
        { type: "reasoning", text: "内部思考" },
        { type: "text", text: "电脑上干完活啦" },
        { type: "tool-call", id: "c1", name: "bash", arguments: { command: "secret" } },
      ],
    },
  },
};

test("真 apply 未配开关：assistant/message 记录无 text（端到端）", () => {
  const records = collectAssistantRecords(undefined, REAL_EVENT);
  assert.equal(records.length, 1, "一条 assistant/message 只写一行");
  assert.ok(!("text" in records[0]), "未配置开关时正文不得落盘");
  assert.equal(records[0].sessionId, "s-e2e");
  assert.equal(records[0].sourceTurn, "3");
});

test("真 apply 配 assistantText: true：记录带截断后的正文（端到端）", () => {
  const records = collectAssistantRecords({ assistantText: true }, REAL_EVENT);
  assert.equal(records.length, 1);
  assert.equal(records[0].text, "电脑上干完活啦", "正文经真接线落盘");
  assert.ok(!records[0].text.includes("内部思考"), "思考块不得当正文");
  assert.ok(!records[0].text.includes("secret"), "工具参数不得当正文");
});

test("真 apply 配 assistantText: false：与未配置同样脱敏（端到端）", () => {
  const records = collectAssistantRecords({ assistantText: false }, REAL_EVENT);
  assert.ok(!("text" in records[0]), "显式关必须与未配置一致");
});

test("真 apply 开开关后，tool/call 记录仍不含命令与参数（端到端回归闸）", () => {
  // 用独立 callId：writtenToolCallIds 是按 callId 去重的模块级集合，
  // 复用上面的 c1 会被判成重复写入而拿不到记录（那样就验不到脱敏字段了）。
  const event = {
    type: "assistant/message",
    data: {
      turn: 3,
      step: 41,
      message: {
        content: [
          { type: "text", text: "顺手跑个命令" },
          { type: "tool-call", id: "c-tool-gate", name: "bash", arguments: { command: "secret-cmd" } },
        ],
      },
    },
  };
  const records = collectAllRecords({ assistantText: true }, event);
  // 同一事件的 tool-call 块仍走脱敏路径：只有工具名，没有命令与参数。
  const allToolRecords = records.filter((rec) => rec.event === "tool/call");
  assert.equal(allToolRecords.length, 1, "工具调用照旧写一条");
  assert.equal(allToolRecords[0].tool, "bash");
  assert.ok(!("command" in allToolRecords[0]), "开正文开关不得顺带放行工具命令");
  assert.ok(!("arguments" in allToolRecords[0]), "开正文开关不得顺带放行工具参数");
  assert.ok(!JSON.stringify(allToolRecords[0]).includes("secret-cmd"), "命令内容不得出现在记录里");
  // 同时，正文开关本身仍然生效
  const assistant = records.filter((rec) => rec.event === "assistant/message");
  assert.equal(assistant[0].text, "顺手跑个命令", "开关仍只放行正文");
});

// ---------------------------------------------------------------- 5. 契约字段对齐
test("text 是桌宠侧既有约定字段名（契约对齐，不改消费端）", async () => {
  // docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md 的 assistant/message 行本就写
  // text；桌宠侧 pet/dsh_state.py 按 record["text"] 读取。这里把这两件事钉成回归：
  // 若哪天有人把字段改名，这个用例必须红，逼他同步消费端。
  const fs = await import("node:fs");
  const repoRoot = path.resolve(here, "..");
  const contract = fs.readFileSync(
    path.join(repoRoot, "docs", "DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md"),
    "utf8",
  );
  assert.ok(/assistant\/message/.test(contract), "契约文档必须提到 assistant/message");
  const statePy = fs.readFileSync(path.join(repoRoot, "pet", "dsh_state.py"), "utf8");
  assert.ok(
    statePy.includes('record.get("text")'),
    '桌宠侧必须仍按 record["text"] 读取助手正文',
  );
});
