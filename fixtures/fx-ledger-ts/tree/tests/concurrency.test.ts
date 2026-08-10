/**
 * The concurrency guarantees.
 *
 * Single-threaded is not the same as race-free. Every `await` is a point where
 * another task runs, so a balance read before one and a write after it is as
 * racy here as it would be with threads. These tests drive genuinely
 * interleaved transfers rather than sequential ones, because sequential calls
 * would pass against a completely unlocked implementation.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { App } from "../src/app.js";
import { InsufficientFundsError } from "../src/errors.js";
import { LockManager } from "../src/locks.js";

const AT = 1_700_000_000_000;

function build(): App {
  const app = new App({ adminToken: "admin-token", clock: () => AT });
  app.openAccount({ id: "equity:opening", type: "equity", currency: "USD" }, AT);
  app.openAccount({ id: "asset:alice", type: "asset", currency: "USD" }, AT);
  app.openAccount({ id: "asset:bob", type: "asset", currency: "USD" }, AT);
  app.openAccount({ id: "asset:carol", type: "asset", currency: "USD" }, AT);
  return app;
}

let fundCounter = 0;

/** Fund an account from equity, which is allowed to go negative. */
function fund(app: App, accountId: string, amount: number): void {
  // Unique per call: funding the same account twice is legitimate, and a fixed
  // id would collide rather than post a second opening entry.
  fundCounter += 1;
  app.ledger.post(
    `tx_fund_${accountId}_${fundCounter}`,
    [
      { accountId: "equity:opening", side: "credit", amount, currency: "USD" },
      { accountId, side: "debit", amount, currency: "USD" },
    ],
    AT,
    "opening balance",
  );
}

describe("lock manager", () => {
  it("serialises holders of one key", async () => {
    const locks = new LockManager();
    const order: string[] = [];

    const first = locks.withLocks(["a"], async () => {
      order.push("first-in");
      await Promise.resolve();
      order.push("first-out");
    });
    const second = locks.withLocks(["a"], () => {
      order.push("second-in");
    });

    await Promise.all([first, second]);
    assert.deepEqual(order, ["first-in", "first-out", "second-in"]);
  });

  it("allows different keys to proceed together", async () => {
    const locks = new LockManager();
    const order: string[] = [];

    await Promise.all([
      locks.withLocks(["a"], async () => {
        order.push("a-in");
        await Promise.resolve();
        order.push("a-out");
      }),
      locks.withLocks(["b"], async () => {
        order.push("b-in");
        await Promise.resolve();
        order.push("b-out");
      }),
    ]);

    // Interleaved rather than serialised: b started before a finished.
    assert.deepEqual(order, ["a-in", "b-in", "a-out", "b-out"]);
  });

  it("does not deadlock on opposing pairs", async () => {
    // Both orderings of the same two keys. Without a deterministic acquisition
    // order each would hold one and wait for the other forever.
    const locks = new LockManager();
    await Promise.all([
      locks.withLocks(["a", "b"], async () => Promise.resolve()),
      locks.withLocks(["b", "a"], async () => Promise.resolve()),
    ]);
    assert.equal(locks.heldCount(), 0);
  });

  it("releases even when the work throws", async () => {
    const locks = new LockManager();
    await assert.rejects(
      locks.withLocks(["a"], () => {
        throw new Error("boom");
      }),
    );
    // A leaked lock would hang this.
    await locks.withLocks(["a"], () => undefined);
    assert.equal(locks.heldCount(), 0);
  });

  it("deduplicates repeated keys", async () => {
    const locks = new LockManager();
    await locks.withLocks(["a", "a"], () => undefined);
    assert.equal(locks.heldCount(), 0);
  });

  it("ignores a second release", async () => {
    // A caller releasing in both a success path and a `finally` would
    // otherwise drive the count negative, and hide a genuinely leaked lock.
    const locks = new LockManager();
    const release = await locks.acquire("a");
    release();
    release();
    assert.equal(locks.heldCount(), 0);
  });

  it("releases the same keys however many times it is called", async () => {
    const locks = new LockManager();
    const release = await locks.acquireAll(["b", "a"]);
    release();
    release();

    assert.equal(locks.heldCount(), 0);
    // Both keys are genuinely free, not merely counted as free.
    await locks.withLocks(["a", "b"], () => undefined);
  });
});

describe("concurrent transfers", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("cannot oversell an account", async () => {
    fund(app, "asset:alice", 100);

    // Two transfers of 60 launched together. One must fail: the account holds
    // 100, and both succeeding would leave it at -20.
    const results = await Promise.allSettled([
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 60, currency: "USD" },
        AT,
      ),
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:carol", amount: 60, currency: "USD" },
        AT,
      ),
    ]);

    const fulfilled = results.filter((result) => result.status === "fulfilled");
    const rejected = results.filter((result) => result.status === "rejected");

    assert.equal(fulfilled.length, 1);
    assert.equal(rejected.length, 1);
    assert.ok((rejected[0] as PromiseRejectedResult).reason instanceof InsufficientFundsError);
    assert.equal(app.ledger.balanceMinorUnits("asset:alice"), 40);
  });

  it("never leaves a balance negative under load", async () => {
    fund(app, "asset:alice", 100);

    // Ten concurrent transfers of 30 against a balance of 100: at most three
    // can succeed.
    const attempts = Array.from({ length: 10 }, () =>
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 30, currency: "USD" },
        AT,
      ),
    );
    const results = await Promise.allSettled(attempts);

    const succeeded = results.filter((result) => result.status === "fulfilled").length;
    assert.equal(succeeded, 3);
    assert.equal(app.ledger.balanceMinorUnits("asset:alice"), 10);
    assert.ok(app.ledger.balanceMinorUnits("asset:alice") >= 0);
  });

  it("keeps the ledger balanced after concurrent transfers", async () => {
    fund(app, "asset:alice", 1000);

    await Promise.allSettled(
      Array.from({ length: 20 }, () =>
        app.transfers.transfer(
          { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 50, currency: "USD" },
          AT,
        ),
      ),
    );

    const trial = app.ledger.trialBalance()["USD"];
    assert.ok(trial !== undefined);
    assert.equal(trial.balanced, true);
    assert.equal(trial.debits, trial.credits);
  });

  it("conserves total value across concurrent transfers", async () => {
    fund(app, "asset:alice", 500);

    await Promise.allSettled(
      Array.from({ length: 15 }, (_unused, index) =>
        app.transfers.transfer(
          {
            fromAccountId: "asset:alice",
            toAccountId: index % 2 === 0 ? "asset:bob" : "asset:carol",
            amount: 40,
            currency: "USD",
          },
          AT,
        ),
      ),
    );

    const total =
      app.ledger.balanceMinorUnits("asset:alice") +
      app.ledger.balanceMinorUnits("asset:bob") +
      app.ledger.balanceMinorUnits("asset:carol");
    assert.equal(total, 500);
  });

  it("allows transfers between disjoint pairs to proceed together", async () => {
    fund(app, "asset:alice", 100);
    fund(app, "asset:bob", 100);

    const results = await Promise.allSettled([
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:carol", amount: 50, currency: "USD" },
        AT,
      ),
      app.transfers.transfer(
        { fromAccountId: "asset:bob", toAccountId: "asset:carol", amount: 50, currency: "USD" },
        AT,
      ),
    ]);

    assert.equal(results.every((result) => result.status === "fulfilled"), true);
    assert.equal(app.ledger.balanceMinorUnits("asset:carol"), 100);
  });

  it("does not deadlock on opposing transfers", async () => {
    fund(app, "asset:alice", 100);
    fund(app, "asset:bob", 100);

    // alice->bob and bob->alice at once. Undeterministic lock ordering would
    // hang here rather than fail.
    const results = await Promise.allSettled([
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 10, currency: "USD" },
        AT,
      ),
      app.transfers.transfer(
        { fromAccountId: "asset:bob", toAccountId: "asset:alice", amount: 10, currency: "USD" },
        AT,
      ),
    ]);

    assert.equal(results.every((result) => result.status === "fulfilled"), true);
    assert.equal(app.locks.heldCount(), 0);
  });

  it("releases locks after a rejected transfer", async () => {
    fund(app, "asset:alice", 10);

    await assert.rejects(
      app.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 100, currency: "USD" },
        AT,
      ),
      InsufficientFundsError,
    );

    // A leaked lock would hang this one.
    fund(app, "asset:alice", 200);
    await app.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 100, currency: "USD" },
      AT,
    );
    assert.equal(app.locks.heldCount(), 0);
  });
});
