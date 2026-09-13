// PR57 遗留缺陷：Watchdog 控制动作打到了子代理身上（打地鼠）。
//
// 根因：当 pet 把 watchdog 控制请求的目标 session 解析为一个 subagent 时，
// bridge 只 cancel 了那个子代理，主 agent 立刻补派新的子代理——用户视角
// 「终止没用」。修复方向：把控制归一到其根 session（interrupt = 停根 agent
// 的当前回合；replan = 给根 agent 注入重规划建议）。
//
// 本测试聚焦新引入的纯函数 `resolveControlRoot`：给定目标 agent 与一个
// session→agent 的查找函数，返回它是否为子代理、是否已归一化到根、根 session
// 与父子链。它不依赖 DSH 运行时（@deepseek-ai/* 未安装时整份模块无法 import），
// 与仓库既有 bridge 契约测试一致：从源码中提取纯函数求值。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
// 实现层：只读 package.json.version 对应的 impl/<version>/index.js（与壳的
// probeDisk 精确选中语义一致，不扫描全部历史版本，避免多版本并存时测到旧版）。
const bridgeRoot = path.resolve(here, "../integrations/dsh-pet-bridge");
const pkg = JSON.parse(fs.readFileSync(path.join(bridgeRoot, "package.json"), "utf8"));
const sourcePath = path.join(bridgeRoot, "impl", String(pkg.version || ""), "index.js");
assert.ok(fs.existsSync(sourcePath), `impl/${pkg.version}/index.js 应存在（与 package.json.version 对应）`);
const source = fs.readFileSync(sourcePath, "utf8");

function loadResolveControlRoot() {
  const match = source.match(/function resolveControlRoot\(agent, sessionLookup\) \{[\s\S]*?\n\}/);
  assert.ok(match, "桥接实现层应定义 resolveControlRoot");
  return new Function(`${match[0]}; return resolveControlRoot;`)();
}

// 构造一个最小的 live agent 桩：id/session.id 与可选的 session.header。
function agent(id, header = undefined) {
  const session = { id };
  if (header) session.header = header;
  return { id, session };
}

function rootSessionIdHeader() {
  // 顶层会话：无 parentSession / 无 delegationDepth。
  return undefined;
}

function subagentHeader(parentSession, delegationDepth = 1) {
  return { parentSession, delegationDepth, origin: "subagent" };
}

test("root session resolves to itself and is not a subagent", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const root = agent("root-1", rootSessionIdHeader());
  const lookup = () => null;
  const r = resolveControlRoot(root, lookup);
  assert.equal(r.wasSubagent, false);
  assert.equal(r.appliedToRoot, false);
  assert.equal(r.rootSessionId, "root-1");
  assert.equal(r.rootAgent, root);
  assert.deepEqual(r.subagentChain, []);
});

test("direct subagent normalizes to its live root", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const root = agent("root-1", rootSessionIdHeader());
  const child = agent("child-1", subagentHeader("root-1"));
  const byId = new Map([
    ["root-1", root],
    ["child-1", child],
  ]);
  const r = resolveControlRoot(child, (id) => byId.get(id) || null);
  assert.equal(r.wasSubagent, true);
  assert.equal(r.appliedToRoot, true);
  assert.equal(r.rootSessionId, "root-1");
  assert.equal(r.rootAgent, root);
  assert.deepEqual(r.subagentChain, ["child-1", "root-1"]);
});

test("grandchild walks the whole lineage to topmost live root", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const grandRoot = agent("root-1", rootSessionIdHeader());
  const parent = agent("child-1", subagentHeader("root-1"));
  const grandChild = agent("child-2", subagentHeader("child-1", 2));
  const byId = new Map([
    ["root-1", grandRoot],
    ["child-1", parent],
    ["child-2", grandChild],
  ]);
  const r = resolveControlRoot(grandChild, (id) => byId.get(id) || null);
  assert.equal(r.wasSubagent, true);
  assert.equal(r.appliedToRoot, true);
  assert.equal(r.rootSessionId, "root-1");
  assert.equal(r.rootAgent, grandRoot);
  assert.deepEqual(r.subagentChain, ["child-2", "child-1", "root-1"]);
});

test("subagent whose parent is not live reports subagent but does not normalize", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const child = agent("child-1", subagentHeader("root-missing"));
  const lookup = () => null; // 父 agent 已不在 liveAgents
  const r = resolveControlRoot(child, lookup);
  assert.equal(r.wasSubagent, true);
  assert.equal(r.appliedToRoot, false);
  assert.equal(r.rootSessionId, "child-1");
  assert.deepEqual(r.subagentChain, ["child-1"]);
});

test("session with no header is treated as a root (defensive)", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const root = agent("root-1"); // 无 session.header
  const r = resolveControlRoot(root, () => null);
  assert.equal(r.wasSubagent, false);
  assert.equal(r.appliedToRoot, false);
  assert.equal(r.rootSessionId, "root-1");
});

test("sibling subagents resolve to the same live root", () => {
  const resolveControlRoot = loadResolveControlRoot();
  const root = agent("root-1", rootSessionIdHeader());
  const childA = agent("child-a", subagentHeader("root-1"));
  const childB = agent("child-b", subagentHeader("root-1"));
  const byId = new Map([
    ["root-1", root],
    ["child-a", childA],
    ["child-b", childB],
  ]);
  const ra = resolveControlRoot(childA, (id) => byId.get(id) || null);
  const rb = resolveControlRoot(childB, (id) => byId.get(id) || null);
  assert.equal(ra.rootSessionId, "root-1");
  assert.equal(rb.rootSessionId, "root-1");
});
