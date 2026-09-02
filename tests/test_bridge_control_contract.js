import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = fs.readFileSync(path.resolve("integrations/dsh-pet-bridge/index.js"), "utf8");

test("control lookup prefers liveAgents and safely catches optional injection", () => {
  assert.match(source, /const live = liveAgents\.get\(id\)/);
  assert.match(source, /ctx\?\.agents\?\.get\?\.\(id\)/);
  assert.match(source, /catch \(err\)[\s\S]*agents lookup unavailable/);
});

test("unknown interrupt cannot cancel and returns explicit not-found", () => {
  assert.match(source, /phase: "not-found"[\s\S]*error: "session-not-found"/);
  assert.match(source, /cancelInvoked: false/);
  assert.match(source, /knownSessions\.has\(sessionId\)/);
});

test("all queue outcomes share the response and event writer", () => {
  assert.match(source, /function writeControlOutcome\(id, request, result\)/);
  assert.match(source, /writeRecord\(\{ event: "bridge\/control-result"/);
  assert.match(source, /writeRecord\(\{ event: "watchdog\/control-result"/);
  assert.match(source, /error: "bridge-internal-error"/);
});

test("question events preserve call and session identity", () => {
  assert.match(source, /function writeQuestionRequest\(callId, questions, sessionId\)/);
  assert.match(source, /writeRecordDedup\(\{[\s\S]*event: "question\/requested"[\s\S]*callId[\s\S]*sessionId[\s\S]*questions/);
  assert.match(source, /function resolveQuestion\(callId, sessionId\)/);
  assert.match(source, /writeRecord\(\{[\s\S]*event: "question\/resolved"[\s\S]*callId[\s\S]*sessionId/);
});
