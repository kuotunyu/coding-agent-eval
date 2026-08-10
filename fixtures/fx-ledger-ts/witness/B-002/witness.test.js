/**
 * Witness for fx-ledger-ts/B-002.
 *
 * Overlaid at run time, never part of `tree/`.
 *
 * The fixture's own suite checks a key just before expiry and well after it,
 * never at the boundary itself. "Expires at T" means live up to T and not at T,
 * and the reader and the purge have to agree about which side T falls on.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { IdempotencyStore } from "../../dist/src/idempotency.js";

const AT = 1_700_000_000_000;
const TTL = 1000;

describe("idempotency key expiry", () => {
  it("treats a key as expired exactly at its expiry", () => {
    const store = new IdempotencyStore(TTL);
    store.remember("order-42", "tr_1", AT);
    assert.equal(
      store.lookup("order-42", AT + TTL),
      undefined,
      "a key was still live at the instant it expired",
    );
  });

  it("keeps the key live right up to that instant", () => {
    const store = new IdempotencyStore(TTL);
    store.remember("order-42", "tr_1", AT);
    assert.ok(store.lookup("order-42", AT + TTL - 1) !== undefined);
  });

  it("purges at the same boundary it reads", () => {
    // A record the reader calls expired must also be one the purge removes, or
    // rows the lookup will never return again accumulate for ever.
    const store = new IdempotencyStore(TTL);
    store.remember("order-42", "tr_1", AT);
    assert.equal(store.purgeExpired(AT + TTL), 1);
  });
});
