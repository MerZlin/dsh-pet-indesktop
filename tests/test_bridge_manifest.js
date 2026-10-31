import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

test("bridge loads standalone (zero external dependencies)", async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")).href);
  assert.equal(typeof bridge.apply, "function");
  // v2 合并聊天通道：需要 agents/sessionQuery/tools；状态事件写入本身不依赖它们。
  assert.deepEqual(bridge.inject, ["agents", "sessionQuery", "tools"]);
});

test("bridge manifest declares no runtime dependencies (zero-dependency red line)", () => {
  // pnpm 的 link: 协议不会安装被链接包自己的依赖，而链接目标常是打包版
  // _internal 副本（无 node_modules）。任何运行时依赖声明都会让用户开联动后
  // 整个 dsh 插件树加载失败（2026-09 事故：缺 @deepseek-ai/dsh-llm）。
  const manifest = JSON.parse(
    fs.readFileSync(path.join(bridgeDir, "package.json"), "utf8"),
  );
  const declared = ["dependencies", "peerDependencies", "optionalDependencies"]
    .flatMap((f) => Object.keys(manifest[f] || {}));
  assert.deepEqual(declared, [], `桥接插件禁止声明运行时依赖: ${declared.join(", ") || "(无)"}`);
});
