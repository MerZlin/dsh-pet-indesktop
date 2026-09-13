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
  // hermeticity 负向对照：从临时目录内部尝试解析一个**在任何 node_modules
  // 都不存在的**包名——探测的是 tmp 的祖先链（ESM 从 canary 文件自身位置
  // 向上解析），若 TEMP/TMPDIR 被外部指到含 node_modules 的树内，这里直接
  // 判红而不是假绿。**不要用真实包名**（如 @deepseek-ai/dsh-llm）当 canary：
  // 用户机器上如果恰好装了该包到 node_modules（例如 C:\Users\<user>\node_modules
  // 里有 dsh-llm，而 TEMP=...\AppData\Local\Temp 的祖先链包含用户目录），
  // canary 会真解析成功，把"环境被污染"误判成"插件不满足 hermetic 前提"。
  const canary = path.join(tmp, "__hermetic_canary__.mjs");
  writeFileSync(
    canary,
    'await import("dsh-pet-bridge-hermetic-canary-never-installed");\nexport {};\n',
    "utf8",
  );
  await assert.rejects(
    import(pathToFileURL(canary).href),
    (err) => err && err.code === "ERR_MODULE_NOT_FOUND",
    "冒烟临时目录的祖先链上存在 node_modules（TEMP/TMPDIR 被污染），hermetic 前提不成立",
  );

  for (const name of ["index.js", "package.json", "cordis.patch.yml"]) {
    const src = path.join(here, name);
    if (existsSync(src)) cpSync(src, path.join(tmp, name));
  }
  // 桥接已拆为稳定壳 + impl/<version>/ 实现层：壳静态 import impl 注册表
  // （ESM 循环导出），临时副本必须带上 impl/ 目录，否则 import 壳即失败。
  const implDir = path.join(here, "impl");
  if (existsSync(implDir)) cpSync(implDir, path.join(tmp, "impl"), { recursive: true });

  // 静态禁令：源码里不允许任何 require/createRequire（CommonJS 逃逸口——
  // 惰性加载可逃过清单与 import 冒烟）。先剥离行注释与块注释（注释里提词面
  // 是常态，不得误伤），再查代码。
  const source = readFileSync(path.join(tmp, "index.js"), "utf8");
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  assert.ok(!/createRequire|[^.\w]require\s*\(/.test(code),
    "index.js 不得使用 require/createRequire（CommonJS 逃逸口）");
  // 动态 import 检查：壳的热重载是**字面量、指向本地 impl 文件的显式**
  // `import(url?t=mtime)`（核心机制，非逃逸）；禁止的是**裸包名/计算型
  // 说明符**（那会让依赖解析逃过零依赖冒烟）。热重载模板用 `${url}...`
  // 模板字符串，剥离它后其余动态 import 一律禁止。
  const hotReloadForm = "await import(`${url}?t=${probe.mtimeMs}`)";
  const remainder = code.replace(hotReloadForm, "");
  assert.ok(!/\bimport\s*\(/.test(remainder),
    "index.js 禁止动态 import（除热重载的 url?t=mtime 字面形态）：惰性加载可绕过清单与门禁");

  // cordis.patch.yml 最低限度 sanity：必须声明桥接 bundle 挂载点。
  const patch = readFileSync(path.join(tmp, "cordis.patch.yml"), "utf8");
  assert.ok(patch.includes("@dsh-pet/bridge") && patch.includes("dsh-pet-bridge"),
    "cordis.patch.yml 必须声明桥接 bundle 挂载点");

  const bridge = await import(pathToFileURL(path.join(tmp, "index.js")).href);

  assert.equal(typeof bridge.apply, "function", "插件应导出 apply(ctx)");
  assert.deepEqual(bridge.inject, ["llm", "agentDefaultModel"]);

  // envelope 形状与 dsh createUserMessage 对齐（llm.stream / steer 直接消费）：
  // role/id 补齐、深冻结、且不回冻调用方传入的对象。
  const input = { content: [{ type: "text", text: "hi" }], source: { kind: "plugin", plugin: "smoke" } };
  const msg = bridge.__messageTest.createUserMessage(input);
  assert.equal(msg.role, "user");
  assert.equal(typeof msg.id, "string");
  assert.ok(msg.id.length > 0);
  assert.deepEqual(msg.content, input.content);
  assert.deepEqual(msg.source, input.source);
  assert.ok(Object.isFrozen(msg) && Object.isFrozen(msg.content) && Object.isFrozen(msg.content[0]));
  assert.ok(!Object.isFrozen(input), "envelope 不得冻结调用方传入的对象");
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log("bridge zero-dependency smoke: import + envelope + source bans OK");
