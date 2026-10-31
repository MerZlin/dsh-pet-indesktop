// 桥接插件零依赖冒烟（hermetic）：把插件拷进一个干净的临时目录——那里
// 保证没有任何 node_modules——再从临时副本 import。等价于 Cordis loader 在
// profile 中加载 link: 链接的真实场景：任何外部 bare import 都会在此暴露，
// 而不是在用户机器上炸掉整个 DSH 插件树（2026-09 事故：打包副本缺
// @deepseek-ai/dsh-llm，dsh web/headless/desktop 全 profile 无法启动）。
// 若在本插件源码目录内直接 import，ESM 向上解析会蹭到本机碰巧装着的
// node_modules，缺依赖被静默掩盖——所以必须拷贝到隔离目录再验。
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// 插件清单必须零依赖（pnpm link: 不安装被链接包的依赖；peer/optional 同理）。
const manifest = JSON.parse(readFileSync(path.join(here, "package.json"), "utf8"));
const depFields = ["dependencies", "peerDependencies", "optionalDependencies"];
const declared = depFields.flatMap((f) => Object.keys(manifest[f] || {}));
assert.deepEqual(
  declared,
  [],
  `桥接插件禁止声明任何运行时依赖（dependencies/peerDependencies/optionalDependencies），现有: ${declared.join(", ") || "(无)"}`,
);

const tmp = mkdtempSync(path.join(tmpdir(), "dsh-pet-bridge-smoke-"));
try {
  // hermeticity 负向对照：从临时目录内部尝试解析历史事故包——探测的是
  // tmp 的祖先链（ESM 从 canary 文件自身位置向上解析），若 TEMP/TMPDIR 被
  // 外部指到含 node_modules 的树内，这里直接判红而不是假绿。
  const canary = path.join(tmp, "__hermetic_canary__.mjs");
  writeFileSync(
    canary,
    'await import("@deepseek-ai/dsh-llm");\nexport {};\n',
    "utf8",
  );
  await assert.rejects(
    import(pathToFileURL(canary).href),
    (err) => err && err.code === "ERR_MODULE_NOT_FOUND",
    "冒烟临时目录的祖先链上存在 node_modules（TEMP/TMPDIR 被污染），hermetic 前提不成立",
  );

  for (const name of ["index.js", "channel.js", "package.json", "cordis.patch.yml"]) {
    const src = path.join(here, name);
    if (existsSync(src)) cpSync(src, path.join(tmp, name));
  }

  // 静态禁令：源码里不允许任何动态 import / require——惰性加载可以逃过
  // 上面的 import 检查（apply() 里的 import() 只有在 DSH host 调用时才执行），
  // 计算型说明符也能逃过字面扫描，因此一律禁止（当前代码为零动态 import）。
  const source = readFileSync(path.join(tmp, "index.js"), "utf8");
  assert.ok(!/createRequire|[^.\w]require\s*\(/.test(source),
    "index.js 不得使用 require/createRequire（CommonJS 逃逸口）");
  assert.ok(!/\bimport\s*\(/.test(source),
    "index.js 不得使用动态 import（含计算型说明符：惰性加载可绕过清单与门禁）");

  // 减法后的事件面静态闸（2026-10）：
  // - mux WebSocket 中继 / 看门狗控制队列 / WATCHDOG 事件转发已删；
  // - 脱敏（#226）：明文正文/命令/参数指纹/结果摘要不落盘，写函数不得存在。
  for (const banned of [
    "muxConnect", "muxSocket", "events.mux",
    "startControlQueue", "handleControlRequest", "runBridgeDiagnosis",
    "CONTROL_POLL_MS", "watchdog-request-",
    "WATCHDOG_EVENT_TYPES",
    "messageText", "commandFromArgs", "summarizeArgs", "extractCommand",
    "latestCommandFor", "argsKey", "resultSummary",
    "createUserMessage", "deepFreezeMessage",
    "session-shape", "rawWorkspace", "rawProject",
  ]) {
    assert.ok(
      !source.includes(banned),
      `index.js 不得再含已删机制/明文字段的标识符: ${banned}`,
    );
  }

  // cordis.patch.yml 最低限度 sanity：必须声明桥接 bundle 挂载点。
  const patch = readFileSync(path.join(tmp, "cordis.patch.yml"), "utf8");
  assert.ok(patch.includes("@dsh-pet/bridge") && patch.includes("dsh-pet-bridge"),
    "cordis.patch.yml 必须声明桥接 bundle 挂载点");

  const bridge = await import(pathToFileURL(path.join(tmp, "index.js")).href);

  assert.equal(typeof bridge.apply, "function", "插件应导出 apply(ctx)");
  // v2 合并聊天通道后需要这三个服务；状态事件写入本身不依赖它们。
  assert.deepEqual(bridge.inject, ["agents", "sessionQuery", "tools"]);
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log("bridge zero-dependency smoke: import + subtraction surface + source bans OK");
