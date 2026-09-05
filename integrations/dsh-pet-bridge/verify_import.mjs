// 桥接插件 import 冒烟：模拟 Cordis loader 的 ESM 解析路径。
// 本文件位于 integrations\dsh-pet-bridge 内，ESM bare specifier 会从本目录
// 向上解析 node_modules，与 Cordis loader 在 profile 中 import 插件时的行为一致。
// 依次 import 插件及其全部直接/传递运行时依赖；任何一个缺包都会抛出
// ERR_MODULE_NOT_FOUND 并让调用方（构建脚本）失败。
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const required = [
  // 插件入口（会经 dsh-llm 的 createUserMessage 链）
  pathToFileURL(resolve(here, "index.js")).href,
  // Cordis 直接依赖 cosmokit / @standard-schema/spec
  "@deepseek-ai/cordis",
  "@deepseek-ai/cosmokit",
  "@standard-schema/spec",
  // dsh-invariants / dsh-llm 运行时依赖 schemastery，schemastery 又依赖 cosmokit
  "@deepseek-ai/schemastery",
  "@deepseek-ai/dsh-llm",
  "@deepseek-ai/dsh-attachment",
  "@deepseek-ai/dsh-brand",
  "@deepseek-ai/dsh-invariants",
  "@deepseek-ai/dsh-timeout",
];

let failed = 0;
for (const spec of required) {
  try {
    await import(spec);
    console.log(`[ok]   ${spec.replace(here + "\\", "")}`);
  } catch (err) {
    failed += 1;
    console.error(`[fail] ${spec.replace(here + "\\", "")}: ${err?.code || ""} ${err?.message || err}`);
  }
}

if (failed > 0) {
  console.error(`bridge import smoke: ${failed} package(s) failed to resolve`);
  process.exit(1);
}
console.log("bridge import smoke: all packages resolved");
