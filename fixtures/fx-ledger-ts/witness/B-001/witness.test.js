/**
 * Witness for fx-ledger-ts/B-001.
 *
 * Overlaid at run time, never part of `tree/`. Plain JavaScript against the
 * built output, so the witness needs no place in the fixture's tsconfig.
 *
 * A batch is all-or-nothing. The fixture's own suite covers the case where the
 * transfer that changed state is the *first* candidate, so the batch fails
 * before marking anything and all-or-nothing holds either way. Nothing covers a
 * later candidate, which is the ordering that tells the two apart.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { App } from "../../dist/src/app.js";
import { ConflictError } from "../../dist/src/errors.js";
import { posting } from "../../dist/src/ledger.js";
import { money } from "../../dist/src/money.js";

const AT = 1_700_000_000_000;

function build() {
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

async function makeTransfers(app, count) {
  for (let index = 0; index < count; index += 1) {
    await app.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 10, currency: "USD" },
      AT,
    );
  }
}

describe("settlement is all or nothing", () => {
  it("applies nothing when a later candidate changed state", async () => {
    const app = build();
    await makeTransfers(app, 3);

    // Candidates are chosen before the locks are taken. Holding a lock opens
    // that window; settling the LAST candidate inside it means a batch without
    // a re-check has already marked the first two by the time it notices.
    const release = await app.locks.acquireAll(["asset:alice"]);
    const running = app.settlement.run(AT);

    const pending = app.transfers.pending();
    const last = pending[pending.length - 1];
    app.transfers.markSettled(last.id, "batch_manual", AT);

    release();
    await assert.rejects(running, ConflictError);

    // The other two must be untouched. Without the re-check they were settled
    // before the conflict was noticed, leaving a partial batch behind.
    assert.equal(app.transfers.pending().length, 2, "a partial batch was applied");
  });

  it("still settles a batch that nothing interferes with", async () => {
    const app = build();
    await makeTransfers(app, 3);
    const result = await app.settlement.run(AT);
    assert.equal(result.settled.length, 3);
  });
});
