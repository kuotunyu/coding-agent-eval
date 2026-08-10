/**
 * Batch settlement.
 *
 * The all-or-nothing property is what these mostly test. Settling part of a
 * batch and stopping would leave a caller unable to tell which transfers went
 * through, and a retry would then double-settle whatever had succeeded.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { App } from "../src/app.js";
import { ConflictError, ValidationError } from "../src/errors.js";
import { posting } from "../src/ledger.js";
import { money } from "../src/money.js";

const AT = 1_700_000_000_000;

function build(): App {
  const app = new App({ adminToken: "admin-token", clock: () => AT });
  app.openAccount({ id: "equity:opening", type: "equity", currency: "USD" }, AT);
  app.openAccount({ id: "asset:alice", type: "asset", currency: "USD" }, AT);
  app.openAccount({ id: "asset:bob", type: "asset", currency: "USD" }, AT);
  app.ledger.post(
    "tx_open",
    [
      posting("equity:opening", "credit", money(100_000, "USD")),
      posting("asset:alice", "debit", money(100_000, "USD")),
    ],
    AT,
    null,
  );
  return app;
}

async function makeTransfers(app: App, count: number): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    await app.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 10, currency: "USD" },
      AT,
    );
  }
}

describe("settlement", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("settles nothing when there is nothing pending", async () => {
    const result = await app.settlement.run(AT);
    assert.equal(result.settled.length, 0);
  });

  it("settles pending transfers", async () => {
    await makeTransfers(app, 3);
    const result = await app.settlement.run(AT);

    assert.equal(result.settled.length, 3);
    assert.equal(result.settled.every((transfer) => transfer.state === "settled"), true);
    assert.equal(app.transfers.pending().length, 0);
  });

  it("stamps a settled transfer with the time", async () => {
    await makeTransfers(app, 1);
    const [settled] = (await app.settlement.run(AT)).settled;
    assert.equal(settled?.settledAt, AT);
  });

  it("respects the batch size", async () => {
    await makeTransfers(app, 5);
    const result = await app.settlement.run(AT, 2);

    assert.equal(result.settled.length, 2);
    assert.equal(app.transfers.pending().length, 3);
  });

  it("settles oldest first", async () => {
    await makeTransfers(app, 3);
    const expected = app.transfers.pending()[0]?.id;
    const result = await app.settlement.run(AT, 1);
    assert.equal(result.settled[0]?.id, expected);
  });

  it("never settles the same transfer twice", async () => {
    await makeTransfers(app, 2);
    await app.settlement.run(AT);

    const second = await app.settlement.run(AT);
    assert.equal(second.settled.length, 0);
  });

  it("refuses a candidate that changed state while the batch waited for locks", async () => {
    // Candidates are chosen before the locks are taken, so a transfer can be
    // settled in that window. The re-check inside the locks is what catches it,
    // and this is the only interleaving that reaches it.
    await makeTransfers(app, 1);

    const release = await app.locks.acquireAll(["asset:alice"]);
    const running = app.settlement.run(AT);

    const [pending] = app.transfers.pending();
    assert.ok(pending !== undefined);
    app.transfers.markSettled(pending.id, "batch_manual", AT);

    release();
    await assert.rejects(running, ConflictError);
  });

  it("leaves nothing settled when a batch is rejected", async () => {
    await makeTransfers(app, 3);

    const release = await app.locks.acquireAll(["asset:alice"]);
    const running = app.settlement.run(AT);

    const [first] = app.transfers.pending();
    assert.ok(first !== undefined);
    app.transfers.markSettled(first.id, "batch_manual", AT);

    release();
    await assert.rejects(running, ConflictError);

    // The other two are untouched: the batch is all-or-nothing.
    assert.equal(app.transfers.pending().length, 2);
  });

  it("gives each batch a distinct id", async () => {
    await makeTransfers(app, 2);
    const first = await app.settlement.run(AT, 1);
    const second = await app.settlement.run(AT, 1);
    assert.notEqual(first.batchId, second.batchId);
  });

  it("records settlement in the journal", async () => {
    await makeTransfers(app, 1);
    const before = app.journal.size();
    await app.settlement.run(AT);
    assert.equal(app.journal.size(), before + 1);
  });

  it("drains everything across batches", async () => {
    await makeTransfers(app, 5);
    const batches = await app.settlement.runUntilDrained(AT, 2);

    assert.equal(batches.length, 3);
    assert.equal(app.transfers.pending().length, 0);
  });

  it("does not change balances", async () => {
    // Money moved when the transfer posted. Settling records that it cleared.
    await makeTransfers(app, 3);
    const before = app.ledger.balanceMinorUnits("asset:alice");
    await app.settlement.run(AT);
    assert.equal(app.ledger.balanceMinorUnits("asset:alice"), before);
  });

  for (const batchSize of [0, -1, 1.5, 501]) {
    it(`rejects a batch size of ${batchSize}`, async () => {
      await assert.rejects(() => app.settlement.run(AT, batchSize), ValidationError);
    });
  }

  it("releases its locks", async () => {
    await makeTransfers(app, 2);
    await app.settlement.run(AT);
    assert.equal(app.locks.heldCount(), 0);
  });

  it("does not block transfers between unrelated accounts", async () => {
    app.openAccount({ id: "asset:carol", type: "asset", currency: "USD" }, AT);
    app.openAccount({ id: "asset:dave", type: "asset", currency: "USD" }, AT);
    app.ledger.post(
      "tx_open_carol",
      [
        posting("equity:opening", "credit", money(500, "USD")),
        posting("asset:carol", "debit", money(500, "USD")),
      ],
      AT,
      null,
    );
    await makeTransfers(app, 2);

    const [settlement, transfer] = await Promise.all([
      app.settlement.run(AT),
      app.transfers.transfer(
        { fromAccountId: "asset:carol", toAccountId: "asset:dave", amount: 100, currency: "USD" },
        AT,
      ),
    ]);

    assert.equal(settlement.settled.length, 2);
    assert.equal(transfer.state, "pending");
  });
});
