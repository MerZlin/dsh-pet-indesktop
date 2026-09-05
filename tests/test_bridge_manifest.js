import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

test("bridge manifest declares the complete Cordis runtime dependency chain", () => {
  const manifest = readJson(path.join(bridgeDir, "package.json"));
  const dependencies = manifest.dependencies || {};
  for (const [name, version] of [
    ["@deepseek-ai/cordis", "^4.0.2"],
    ["@deepseek-ai/cosmokit", "^1.8.3"],
    ["@deepseek-ai/dsh-attachment", "^0.1.1-rc.2"],
    ["@deepseek-ai/dsh-brand", "^0.1.1-rc.2"],
    ["@deepseek-ai/dsh-invariants", "^0.1.1-rc.2"],
    ["@deepseek-ai/dsh-llm", "^0.1.1-rc.2"],
    ["@deepseek-ai/dsh-timeout", "^0.1.1-rc.2"],
    ["@deepseek-ai/schemastery", "^3.18.2"],
    ["@standard-schema/spec", "^1.1.0"],
  ]) assert.equal(dependencies[name], version, `${name} runtime dependency`);

  const lockfile = fs.readFileSync(path.join(bridgeDir, "pnpm-lock.yaml"), "utf8");
  for (const packageName of [
    "@deepseek-ai/cosmokit@1.8.3",
    "@deepseek-ai/schemastery@3.18.2",
    "@standard-schema/spec@1.1.0",
  ]) assert.match(lockfile, new RegExp(packageName.replace(/[\\/]/g, "\\$&")));
});

test("bridge loads with its dependency in a standalone-style package directory", async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  assert.equal(typeof bridge.apply, "function");
  assert.deepEqual(bridge.inject, ["llm", "agentDefaultModel"]);
});
