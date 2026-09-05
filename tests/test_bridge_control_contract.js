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

test("approval/asked is audit-only and never upgrades to a UI approval request", () => {
  // The audit event must stay in the state/audit forwarding set (dsh_state latch),
  // but the bridge must no longer turn it into a Pet approval popup.
  assert.match(source, /"approval\/asked",/);
  // No writeApprovalRequest helper exists anymore.
  assert.doesNotMatch(source, /writeApprovalRequest\s*\(/);
  // No session/event branch treats approval/asked as a UI request source.
  assert.doesNotMatch(source, /if \(type === "approval\/asked"\)/);
  // The only approval/request writer is the mux relay (see next tests).
});

test("mux approval/requested requires rpcId and sessionId before writing approval/request", () => {
  assert.match(source, /if \(p\.type === "approval\/requested"\)[\s\S]{0,400}if \(rpcId && sessionId\)/);
  assert.match(source, /writeRecordDedup\(\{ event: "approval\/request", rpcId, sessionId, approvalId:/);
  assert.match(source, /忽略缺身份的 mux 审批帧/);
  assert.match(source, /const rpcId = String\(msg\.rpcId \|\| ""\)/);
  assert.match(source, /const sessionId = String\(p\.sessionId \|\| ""\)/);
});

test("mux approval/resolved carries the same identity for exact pairing", () => {
  assert.match(source, /writeRecord\(\{ event: "approval\/resolved", rpcId: msg\.rpcId, sessionId: p\.sessionId, approvalId: p\.approvalId, outcome: p\.outcome \}\)/);
  assert.match(source, /writeInteractionResolved\("approval", p\.sessionId, \{ rpcId: msg\.rpcId, approvalId: p\.approvalId \}, p\.outcome \|\| "approved"\)/);
});

test("question events preserve call and session identity", () => {
  assert.match(source, /function writeQuestionRequest\(callId, questions, sessionId\)/);
  assert.match(source, /writeRecordDedup\(\{[\s\S]*event: "question\/requested"[\s\S]*callId[\s\S]*sessionId[\s\S]*questions/);
  assert.match(source, /function resolveQuestion\(callId, sessionId\)/);
  assert.match(source, /writeRecord\(\{[\s\S]*event: "question\/resolved"[\s\S]*callId[\s\S]*sessionId/);
});
