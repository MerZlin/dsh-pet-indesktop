// F6 回归：tool/result 收敛 ask_user_question 时不得再写不可达的 user_action。
//
// 背景：桥接在 tool/result 里先调 resolveQuestion(callId, sessionId)——它的第一
// 动作就是按复合键 sessionId|callId 删掉 pendingQuestionCallIds；紧随其后的
// `pendingQuestionCallIds.has(String(callId))` 既因 pending 键已被删除、又因复合键
// 无法用裸 callId 命中，恒为 False，那段 user_action 写盘永远不会执行。而
// question/resolved 已由 resolveQuestion 内部负责写出（桌宠按它收尾问题气泡），
// 留着这段只能误导后续维护者去修一个到不了的分支。
//
// 与仓库既有 bridge 契约测试一致，这里从源码提取 tool/result 分支并断言形态。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeRoot = path.resolve(here, "../integrations/dsh-pet-bridge");
const pkg = JSON.parse(fs.readFileSync(path.join(bridgeRoot, "package.json"), "utf8"));
const sourcePath = path.join(bridgeRoot, "impl", String(pkg.version || ""), "index.js");
const source = fs.readFileSync(sourcePath, "utf8");

// 截取 tool/result 分支里处理 ask_user_question 收敛的那一段：
// 从分支开头到 toolResultInfo 解析（其后的逻辑与会话问题无关）。
function toolResultQuestionBlock() {
  const start = source.indexOf('} else if (type === "tool/result") {');
  assert.ok(start >= 0, "index.js 应保留 tool/result 处理分支");
  const end = source.indexOf("const info = toolResultInfo(d);", start);
  assert.ok(end > start, "tool/result 分支应仍以 toolResultInfo 解析结果");
  return source.slice(start, end);
}

// 注释里可以说明「已删除某段写盘」，断言只看真实代码（去掉整行注释）。
function stripLineComments(text) {
  return text.replace(/^[ \t]*\/\/.*$/gm, "");
}

test("tool/result 问题收敛保留 resolveQuestion 写盘路径", () => {
  const block = stripLineComments(toolResultQuestionBlock());
  assert.match(
    block,
    /if \(callId\) resolveQuestion\(callId, sessionId\);/,
    "resolveQuestion 是 question/resolved 的唯一写盘路径，必须保留",
  );
});

test("tool/result 不再写恒不可达的 user_action(question_resolved)", () => {
  const block = stripLineComments(toolResultQuestionBlock());
  assert.ok(
    !/user_action/.test(block),
    "resolveQuestion 已删 pending 键、且键为复合键 sessionId|callId，"
      + "裸 callId 的 user_action 兜底恒不可达，必须删除",
  );
});

test("模块内不再用裸 callId 匹配复合键 pendingQuestionCallIds", () => {
  assert.ok(
    !/pendingQuestionCallIds\.has\(String\(callId\)\)/.test(stripLineComments(source)),
    "pendingQuestionCallIds 的键是 sessionId|callId 复合键，不得用裸 callId 匹配",
  );
});
