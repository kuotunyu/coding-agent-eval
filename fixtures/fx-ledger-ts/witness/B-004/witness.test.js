/**
 * Witness for fx-ledger-ts/B-004.
 *
 * Overlaid at run time, never part of `tree/`.
 *
 * The timing property itself cannot be measured reliably in a test — that needs
 * statistics and a quiet machine. What is checked is the thing that actually
 * regresses: that a length mismatch is still compared rather than returned on
 * immediately, because an immediate return is what makes the rejection fast
 * enough to reveal how long the configured token is.
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { describe, it, mock } from "node:test";

import { verifyToken } from "../../dist/src/auth.js";
import { UnauthorizedError } from "../../dist/src/errors.js";

const TOKEN = "s3cret-admin-token";

describe("token comparison", () => {
  it("compares even when the lengths differ", () => {
    const spy = mock.method(crypto, "timingSafeEqual");
    try {
      assert.throws(() => verifyToken("short", TOKEN), UnauthorizedError);
      assert.ok(
        spy.mock.callCount() > 0,
        "a length mismatch was rejected without comparing anything",
      );
    } finally {
      spy.mock.restore();
    }
  });

  it("still compares when the lengths match", () => {
    const spy = mock.method(crypto, "timingSafeEqual");
    try {
      verifyToken(TOKEN, TOKEN);
      assert.ok(spy.mock.callCount() > 0);
    } finally {
      spy.mock.restore();
    }
  });

  it("rejects both a short and a long token", () => {
    assert.throws(() => verifyToken(TOKEN.slice(0, 4), TOKEN), UnauthorizedError);
    assert.throws(() => verifyToken(`${TOKEN}extra`, TOKEN), UnauthorizedError);
  });
});
