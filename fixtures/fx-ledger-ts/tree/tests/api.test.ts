/** HTTP routing, authentication, and status codes. */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { App } from "../src/app.js";
import type { Request, Response } from "../src/api.js";
import { posting } from "../src/ledger.js";
import { money } from "../src/money.js";

const AT = 1_700_000_000_000;
const TOKEN = "admin-token";

const ADMIN_ROUTES: readonly (readonly [string, string])[] = [
  ["POST", "/accounts"],
  ["POST", "/settlement/run"],
  ["GET", "/admin/trial-balance"],
];

function build(): App {
  const app = new App({ adminToken: TOKEN, clock: () => AT });
  app.openAccount({ id: "equity:opening", type: "equity", currency: "USD" }, AT);
  app.openAccount({ id: "asset:alice", type: "asset", currency: "USD" }, AT);
  app.openAccount({ id: "asset:bob", type: "asset", currency: "USD" }, AT);
  app.ledger.post(
    "tx_open",
    [
      posting("equity:opening", "credit", money(1000, "USD")),
      posting("asset:alice", "debit", money(1000, "USD")),
    ],
    AT,
    null,
  );
  return app;
}

async function call(
  app: App,
  method: string,
  path: string,
  body: unknown = undefined,
  headers: Record<string, string> = {},
): Promise<Response> {
  const request: Request = {
    method,
    path,
    body: body === undefined ? "" : JSON.stringify(body),
    headers,
  };
  return app.api.handle(request);
}

function admin(): Record<string, string> {
  return { authorization: `Bearer ${TOKEN}` };
}

describe("routing", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("answers health without authentication", async () => {
    const response = await call(app, "GET", "/health");
    assert.equal(response.status, 200);
  });

  it("returns 404 for an unknown path", async () => {
    assert.equal((await call(app, "GET", "/nope")).status, 404);
  });

  it("returns 405 for a known path with the wrong method", async () => {
    assert.equal((await call(app, "GET", "/transfers")).status, 405);
  });

  it("does not match a path with a traversal or a slash in a segment", async () => {
    for (const path of [
      "/accounts/../admin/trial-balance",
      "/accounts/UPPER",
      "/accounts/ab",
      "/transfers/not-a-transfer-id",
    ]) {
      const response = await call(app, "GET", path);
      assert.ok(response.status === 404 || response.status === 405, path);
    }
  });

  it("rejects a body that is not JSON", async () => {
    const response = await app.api.handle({
      method: "POST",
      path: "/transfers",
      body: "{oops",
      headers: {},
    });
    assert.equal(response.status, 400);
  });

  it("rejects a body that is a JSON array", async () => {
    const response = await app.api.handle({
      method: "POST",
      path: "/transfers",
      body: "[1,2,3]",
      headers: {},
    });
    assert.equal(response.status, 400);
  });

  it("rejects an oversized body", async () => {
    const response = await app.api.handle({
      method: "POST",
      path: "/transfers",
      body: JSON.stringify({ pad: "x".repeat(100_000) }),
      headers: {},
    });
    assert.equal(response.status, 413);
  });
});

describe("authentication", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  for (const [method, path] of ADMIN_ROUTES) {
    it(`requires a token for ${method} ${path}`, async () => {
      assert.equal((await call(app, method, path, {})).status, 401);
    });

    it(`rejects a wrong token for ${method} ${path}`, async () => {
      const response = await call(app, method, path, {}, { authorization: "Bearer wrong" });
      assert.equal(response.status, 401);
    });
  }

  it("answers a missing and a wrong token identically", async () => {
    const missing = await call(app, "GET", "/admin/trial-balance");
    const wrong = await call(app, "GET", "/admin/trial-balance", undefined, {
      authorization: "Bearer wrong",
    });
    assert.equal(missing.status, wrong.status);
    assert.deepEqual(missing.body, wrong.body);
  });
});

describe("accounts", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("opens an account", async () => {
    const response = await call(
      app,
      "POST",
      "/accounts",
      { id: "asset:dave", type: "asset", currency: "USD" },
      admin(),
    );
    assert.equal(response.status, 201);
  });

  it("rejects a duplicate id", async () => {
    const body = { id: "asset:alice", type: "asset", currency: "USD" };
    assert.equal((await call(app, "POST", "/accounts", body, admin())).status, 409);
  });

  it("rejects an unknown type", async () => {
    const body = { id: "asset:dave", type: "wormhole", currency: "USD" };
    assert.equal((await call(app, "POST", "/accounts", body, admin())).status, 400);
  });

  it("rejects an unsupported currency", async () => {
    const body = { id: "asset:dave", type: "asset", currency: "XYZ" };
    assert.equal((await call(app, "POST", "/accounts", body, admin())).status, 400);
  });

  it("returns an account with its balance", async () => {
    const response = await call(app, "GET", "/accounts/asset:alice");
    assert.equal(response.status, 200);
    assert.equal((response.body as { balanceMinorUnits: number }).balanceMinorUnits, 1000);
  });

  it("returns 404 for an unknown account", async () => {
    assert.equal((await call(app, "GET", "/accounts/asset:nobody")).status, 404);
  });
});

describe("transfers", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("creates a transfer", async () => {
    const response = await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 250,
      currency: "USD",
    });
    assert.equal(response.status, 201);
    assert.equal((response.body as { state: string }).state, "pending");
  });

  it("moves the balance", async () => {
    await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 250,
      currency: "USD",
    });
    assert.equal(app.ledger.balanceMinorUnits("asset:alice"), 750);
    assert.equal(app.ledger.balanceMinorUnits("asset:bob"), 250);
  });

  it("rejects a transfer larger than the balance", async () => {
    const response = await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 5000,
      currency: "USD",
    });
    assert.equal(response.status, 422);
    assert.equal((response.body as { error: string }).error, "insufficient_funds");
  });

  it("rejects a fractional amount", async () => {
    const response = await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 12.5,
      currency: "USD",
    });
    assert.equal(response.status, 400);
  });

  it("rejects a transfer to the same account", async () => {
    const response = await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:alice",
      amount: 10,
      currency: "USD",
    });
    assert.equal(response.status, 400);
  });

  it("deduplicates on the idempotency key header", async () => {
    const body = {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 100,
      currency: "USD",
    };
    const headers = { "idempotency-key": "order-42" };
    const first = await call(app, "POST", "/transfers", body, headers);
    const second = await call(app, "POST", "/transfers", body, headers);

    assert.equal((first.body as { id: string }).id, (second.body as { id: string }).id);
    assert.equal(app.ledger.balanceMinorUnits("asset:alice"), 900);
  });

  it("returns a transfer by id", async () => {
    const created = await call(app, "POST", "/transfers", {
      fromAccountId: "asset:alice",
      toAccountId: "asset:bob",
      amount: 100,
      currency: "USD",
    });
    const id = (created.body as { id: string }).id;
    assert.equal((await call(app, "GET", `/transfers/${id}`)).status, 200);
  });
});

describe("trial balance endpoint", () => {
  it("reports balanced currencies", async () => {
    const app = build();
    const response = await call(app, "GET", "/admin/trial-balance", undefined, admin());
    assert.equal(response.status, 200);
    const currencies = (response.body as { currencies: Record<string, { balanced: boolean }> })
      .currencies;
    assert.equal(currencies["USD"]?.balanced, true);
  });
});
