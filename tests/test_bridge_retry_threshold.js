import assert from "node:assert/strict";
import test from "node:test";

import { __retryTest } from "../integrations/dsh-pet-bridge/index.js";

test("same session emits only on the fifth consecutive retry", () => {
  const session = "retry-threshold-session";
  __retryTest.reset(session);

  for (let count = 1; count < __retryTest.threshold; count += 1) {
    assert.equal(__retryTest.note(session), false);
  }
  assert.equal(__retryTest.note(session), true);
  assert.equal(__retryTest.note(session), false);
});

test("retry counts are isolated per session", () => {
  const a = "retry-session-a";
  const b = "retry-session-b";
  __retryTest.reset(a);
  __retryTest.reset(b);

  for (let count = 0; count < 4; count += 1) {
    assert.equal(__retryTest.note(a), false);
    assert.equal(__retryTest.note(b), false);
  }
  assert.equal(__retryTest.note(a), true);
  assert.equal(__retryTest.note(b), true);
});

test("a successful or other event resets the retry streak", () => {
  const session = "retry-reset-session";
  __retryTest.reset(session);

  for (let count = 0; count < 3; count += 1) {
    assert.equal(__retryTest.note(session), false);
  }
  __retryTest.reset(session);
  for (let count = 0; count < 4; count += 1) {
    assert.equal(__retryTest.note(session), false);
  }
  assert.equal(__retryTest.note(session), true);
});
