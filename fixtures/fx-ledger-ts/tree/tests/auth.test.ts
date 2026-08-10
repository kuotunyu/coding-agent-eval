/**
 * Authentication and idempotency.
 *
 * The timing property cannot be proven here — measuring it needs statistics and
 * a quiet machine. What is tested is the thing that actually regresses: that
 * comparison goes through `timingSafeEqual` rather than `===`.
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { describe, it, mock } from "node:test";

import { authenticate, extractBearerToken, verifyToken } from "../src/auth.js";
import { UnauthorizedError, ValidationError } from "../src/errors.js";
import { IdempotencyStore, validateKey } from "../src/idempotency.js";

const TOKEN = "s3cret-admin-token";
const AT = 1_700_000_000_000;

describe("bearer extraction", () => {
  it("extracts a token", () => {
    assert.equal(extractBearerToken(`Bearer ${TOKEN}`), TOKEN);
  });

  it("trims surrounding whitespace", () => {
    assert.equal(extractBearerToken(`Bearer  ${TOKEN}  `), TOKEN);
  });

  for (const header of [null, undefined, "", "Basic abc", "bearer lower", "Bearer", "Bearer   "]) {
    it(`returns null for ${JSON.stringify(header)}`, () => {
      assert.equal(extractBearerToken(header), null);
    });
  }
});

describe("verification", () => {
  it("accepts the right token", () => {
    verifyToken(TOKEN, TOKEN);
  });

  it("rejects the wrong token", () => {
    assert.throws(() => verifyToken("wrong", TOKEN), UnauthorizedError);
  });

  it("rejects a token differing only in the last byte", () => {
    assert.throws(() => verifyToken(`${TOKEN.slice(0, -1)}X`, TOKEN), UnauthorizedError);
  });

  it("rejects a prefix of the token", () => {
    assert.throws(() => verifyToken(TOKEN.slice(0, 5), TOKEN), UnauthorizedError);
  });

  it("rejects a longer token with the right prefix", () => {
    assert.throws(() => verifyToken(`${TOKEN}extra`, TOKEN), UnauthorizedError);
  });

  it("answers a missing and a wrong token identically", () => {
    let missing = "";
    let wrong = "";
    try {
      verifyToken(null, TOKEN);
    } catch (error) {
      missing = (error as Error).message;
    }
    try {
      verifyToken("wrong", TOKEN);
    } catch (error) {
      wrong = (error as Error).message;
    }
    assert.equal(missing, wrong);
  });

  it("closes the routes when no token is configured", () => {
    // An unset secret is an unfinished deployment, not an open one.
    assert.throws(() => verifyToken("anything", null), UnauthorizedError);
    assert.throws(() => verifyToken("anything", ""), UnauthorizedError);
    assert.throws(() => verifyToken(null, null), UnauthorizedError);
  });

  it("accepts a full header", () => {
    authenticate(`Bearer ${TOKEN}`, TOKEN);
  });

  it("rejects a missing header", () => {
    assert.throws(() => authenticate(undefined, TOKEN), UnauthorizedError);
  });

  it("goes through timingSafeEqual", () => {
    // The realistic regression is someone replacing this with ===.
    const spy = mock.method(crypto, "timingSafeEqual");
    try {
      verifyToken(TOKEN, TOKEN);
      assert.ok(spy.mock.callCount() > 0, "comparison did not use timingSafeEqual");
    } finally {
      spy.mock.restore();
    }
  });

  it("handles a non-ascii token", () => {
    const unicode = "tökén-ünïcodé";
    verifyToken(unicode, unicode);
    assert.throws(() => verifyToken(unicode, `${unicode}x`), UnauthorizedError);
  });
});

describe("idempotency keys", () => {
  it("accepts a well-formed key", () => {
    assert.equal(validateKey("order-42"), "order-42");
  });

  for (const key of ["", "-leading", "has space", "x".repeat(129), 42, null]) {
    it(`rejects ${JSON.stringify(key)}`, () => {
      assert.throws(() => validateKey(key), ValidationError);
    });
  }

  it("returns a remembered record", () => {
    const store = new IdempotencyStore();
    store.remember("order-42", "tr_1", AT);
    assert.equal(store.lookup("order-42", AT)?.transferId, "tr_1");
  });

  it("returns undefined for an unknown key", () => {
    assert.equal(new IdempotencyStore().lookup("order-42", AT), undefined);
  });

  it("treats an expired key as absent", () => {
    const store = new IdempotencyStore(1000);
    store.remember("order-42", "tr_1", AT);
    assert.equal(store.lookup("order-42", AT + 1001), undefined);
  });

  it("keeps a key live right up to expiry", () => {
    const store = new IdempotencyStore(1000);
    store.remember("order-42", "tr_1", AT);
    assert.ok(store.lookup("order-42", AT + 999) !== undefined);
  });

  it("purges only expired keys", () => {
    const store = new IdempotencyStore(1000);
    store.remember("old", "tr_1", AT);
    store.remember("new", "tr_2", AT + 900);

    assert.equal(store.purgeExpired(AT + 1001), 1);
    assert.equal(store.size(), 1);
  });
});
