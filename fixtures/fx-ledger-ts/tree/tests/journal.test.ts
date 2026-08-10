/**
 * The journal, and replay from it.
 *
 * Replay is what makes the journal worth keeping: state is a function of the
 * record. If the two can disagree, the record is decoration.
 */

import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";

import { App, restore } from "../src/app.js";
import { NotFoundError, ValidationError } from "../src/errors.js";
import { FileJournal, MemoryJournal, validateSequence } from "../src/journal.js";
import { posting } from "../src/ledger.js";
import { money } from "../src/money.js";

const AT = 1_700_000_000_000;

function seed(app: App): void {
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
}

describe("memory journal", () => {
  it("numbers entries from one", () => {
    const journal = new MemoryJournal();
    const first = journal.append({ kind: "transfer_settled", at: AT, transferId: "t1", batchId: "b1" });
    const second = journal.append({ kind: "transfer_settled", at: AT, transferId: "t2", batchId: "b1" });
    assert.equal(first.seq, 1);
    assert.equal(second.seq, 2);
  });

  it("returns a copy, so a caller cannot rewrite history", () => {
    const journal = new MemoryJournal();
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t1", batchId: "b1" });
    const entries = journal.read() as unknown[];
    entries.length = 0;
    assert.equal(journal.size(), 1);
  });
});

describe("sequence validation", () => {
  it("accepts a contiguous journal", () => {
    const journal = new MemoryJournal();
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t1", batchId: "b1" });
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t2", batchId: "b1" });
    validateSequence(journal.read());
  });

  it("rejects a journal with a gap", () => {
    // A gap means an entry was lost, and replaying across it would produce a
    // state that never existed.
    const entries = [
      { kind: "transfer_settled", seq: 1, at: AT, transferId: "t1", batchId: "b1" },
      { kind: "transfer_settled", seq: 3, at: AT, transferId: "t3", batchId: "b1" },
    ] as const;
    assert.throws(() => validateSequence(entries), ValidationError);
  });
});

describe("file journal", () => {
  let directory: string;
  let path: string;

  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), "ledger-"));
    path = join(directory, "journal.jsonl");
  });

  afterEach(() => {
    rmSync(directory, { recursive: true, force: true });
  });

  it("reads an absent file as empty", () => {
    assert.equal(new FileJournal(path).read().length, 0);
  });

  it("appends one line per entry", () => {
    const journal = new FileJournal(path);
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t1", batchId: "b1" });
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t2", batchId: "b1" });

    const lines = readFileSync(path, "utf8").trim().split("\n");
    assert.equal(lines.length, 2);
  });

  it("continues numbering after reopening", () => {
    new FileJournal(path).append({
      kind: "transfer_settled",
      at: AT,
      transferId: "t1",
      batchId: "b1",
    });
    const reopened = new FileJournal(path);
    const entry = reopened.append({
      kind: "transfer_settled",
      at: AT,
      transferId: "t2",
      batchId: "b1",
    });
    assert.equal(entry.seq, 2);
  });

  it("rejects a corrupt line rather than skipping it", () => {
    const journal = new FileJournal(path);
    journal.append({ kind: "transfer_settled", at: AT, transferId: "t1", batchId: "b1" });
    // Appending garbage directly, as a truncated write would leave behind.
    appendFileSync(path, "{not json\n", "utf8");

    assert.throws(() => new FileJournal(path).read(), ValidationError);
  });
});

describe("replay", () => {
  it("reproduces accounts and balances", () => {
    const journal = new MemoryJournal();
    const original = new App({ journal, clock: () => AT });
    seed(original);

    const restored = restore(journal, { clock: () => AT });
    assert.equal(restored.accounts.size(), 3);
    assert.equal(restored.ledger.balanceMinorUnits("asset:alice"), 1000);
  });

  it("reproduces balances after transfers", async () => {
    const journal = new MemoryJournal();
    const original = new App({ journal, clock: () => AT });
    seed(original);
    await original.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 250, currency: "USD" },
      AT,
    );

    const restored = restore(journal, { clock: () => AT });
    assert.equal(restored.ledger.balanceMinorUnits("asset:alice"), 750);
    assert.equal(restored.ledger.balanceMinorUnits("asset:bob"), 250);
  });

  it("reproduces a balanced trial balance", async () => {
    const journal = new MemoryJournal();
    const original = new App({ journal, clock: () => AT });
    seed(original);
    await original.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 100, currency: "USD" },
      AT,
    );

    const restored = restore(journal, { clock: () => AT });
    assert.equal(restored.ledger.trialBalance()["USD"]?.balanced, true);
  });

  it("is idempotent across two restores", async () => {
    const journal = new MemoryJournal();
    const original = new App({ journal, clock: () => AT });
    seed(original);
    await original.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 100, currency: "USD" },
      AT,
    );

    const first = restore(journal, { clock: () => AT });
    const second = restore(journal, { clock: () => AT });
    assert.equal(
      first.ledger.balanceMinorUnits("asset:alice"),
      second.ledger.balanceMinorUnits("asset:alice"),
    );
  });

  it("refuses to replay a journal with a gap", () => {
    const app = new App({ clock: () => AT });
    assert.throws(
      () =>
        app.replay([
          { kind: "transfer_settled", seq: 2, at: AT, transferId: "t1", batchId: "b1" },
        ]),
      ValidationError,
    );
  });

  it("survives a round trip through a file", async () => {
    const directory = mkdtempSync(join(tmpdir(), "ledger-"));
    try {
      const path = join(directory, "journal.jsonl");
      const original = new App({ journal: new FileJournal(path), clock: () => AT });
      seed(original);
      await original.transfers.transfer(
        { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 400, currency: "USD" },
        AT,
      );

      const restored = restore(new FileJournal(path), { clock: () => AT });
      assert.equal(restored.ledger.balanceMinorUnits("asset:bob"), 400);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});

describe("replaying transfers", () => {
  /** Seed an app, make one transfer, and return the journal and the transfer. */
  async function withOneTransfer(): Promise<{ journal: MemoryJournal; transferId: string }> {
    const journal = new MemoryJournal();
    const original = new App({ journal, clock: () => AT });
    seed(original);
    const transfer = await original.transfers.transfer(
      {
        fromAccountId: "asset:alice",
        toAccountId: "asset:bob",
        amount: 300,
        currency: "USD",
        reference: "invoice 7",
      },
      AT,
    );
    return { journal, transferId: transfer.id };
  }

  it("restores a pending transfer", async () => {
    // Balances alone are not the state. A transfer that survived as a balance
    // change but not as a transfer is money moved on nobody's instruction.
    const { journal, transferId } = await withOneTransfer();

    const restored = restore(journal, { clock: () => AT });
    assert.equal(restored.transfers.count(), 1);
    assert.equal(restored.transfers.get(transferId).state, "pending");
  });

  it("restores the whole transfer, not just its identity", async () => {
    const { journal, transferId } = await withOneTransfer();

    const transfer = restore(journal, { clock: () => AT }).transfers.get(transferId);
    assert.equal(transfer.fromAccountId, "asset:alice");
    assert.equal(transfer.toAccountId, "asset:bob");
    assert.equal(transfer.amount, 300);
    assert.equal(transfer.reference, "invoice 7");
  });

  it("restores a settled transfer as settled", async () => {
    const { journal, transferId } = await withOneTransfer();
    const original = restore(journal, { clock: () => AT });
    await original.settlement.run(AT);

    const restored = restore(journal, { clock: () => AT });
    assert.equal(restored.transfers.get(transferId).state, "settled");
    assert.equal(restored.transfers.get(transferId).settledAt, AT);
    assert.equal(restored.transfers.pending().length, 0);
  });

  it("can settle a transfer made before the restart", async () => {
    const { journal, transferId } = await withOneTransfer();

    const restored = restore(journal, { clock: () => AT });
    const result = await restored.settlement.run(AT);

    assert.equal(result.settled.length, 1);
    assert.equal(result.settled[0]?.id, transferId);
  });

  it("does not reissue an id it just replayed", async () => {
    // The counter restarts with the process. Without carrying it across the
    // replay, the first transfer after a restart takes the id of the first
    // transfer before it — and the clock is injected here, so the timestamp
    // component does not save it.
    const { journal, transferId } = await withOneTransfer();
    const restored = restore(journal, { clock: () => AT });

    const next = await restored.transfers.transfer(
      { fromAccountId: "asset:alice", toAccountId: "asset:bob", amount: 10, currency: "USD" },
      AT,
    );
    assert.notEqual(next.id, transferId);
    assert.equal(restored.transfers.count(), 2);
  });

  it("does not append to the journal it is replaying", async () => {
    // Replay reads the record. An append during it would grow the file on
    // every start, and the journal would eventually be mostly its own echo.
    const { journal } = await withOneTransfer();
    const before = journal.size();

    restore(journal, { clock: () => AT });
    assert.equal(journal.size(), before);
  });

  it("refuses a settlement entry with no matching transfer", () => {
    // A journal that records a transfer settling without recording the
    // transfer is not a history that can be replayed approximately.
    const app = new App({ clock: () => AT });
    assert.throws(
      () =>
        app.replay([
          { kind: "transfer_settled", seq: 1, at: AT, transferId: "tr_nope", batchId: "b1" },
        ]),
      NotFoundError,
    );
  });
});
