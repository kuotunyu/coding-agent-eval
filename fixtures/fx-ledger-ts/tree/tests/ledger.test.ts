/** Double-entry posting, balances, and the trial balance. */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { increasesOn, normalSide } from "../src/accounts.js";
import { App } from "../src/app.js";
import { ConflictError, NotFoundError, UnbalancedError, ValidationError } from "../src/errors.js";
import { assertBalanced, posting } from "../src/ledger.js";
import { money } from "../src/money.js";

const AT = 1_700_000_000_000;

function build(): App {
  const app = new App({ clock: () => AT });
  app.openAccount({ id: "asset:cash", type: "asset", currency: "USD" }, AT);
  app.openAccount({ id: "revenue:sales", type: "revenue", currency: "USD" }, AT);
  app.openAccount({ id: "expense:rent", type: "expense", currency: "USD" }, AT);
  app.openAccount({ id: "liability:loan", type: "liability", currency: "USD" }, AT);
  return app;
}

describe("normal sides", () => {
  it("puts assets and expenses on the debit side", () => {
    assert.equal(normalSide("asset"), "debit");
    assert.equal(normalSide("expense"), "debit");
  });

  it("puts liabilities, equity, and revenue on the credit side", () => {
    assert.equal(normalSide("liability"), "credit");
    assert.equal(normalSide("equity"), "credit");
    assert.equal(normalSide("revenue"), "credit");
  });

  it("knows which side increases each type", () => {
    assert.equal(increasesOn("asset", "debit"), true);
    assert.equal(increasesOn("asset", "credit"), false);
    assert.equal(increasesOn("revenue", "credit"), true);
  });
});

describe("balance checking", () => {
  it("accepts a balanced pair", () => {
    assert.equal(
      assertBalanced([
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(100, "USD")),
      ]),
      "USD",
    );
  });

  it("rejects an unbalanced pair", () => {
    assert.throws(
      () =>
        assertBalanced([
          posting("asset:cash", "debit", money(100, "USD")),
          posting("revenue:sales", "credit", money(99, "USD")),
        ]),
      UnbalancedError,
    );
  });

  it("rejects fewer than two postings", () => {
    assert.throws(() => assertBalanced([]), UnbalancedError);
    assert.throws(
      () => assertBalanced([posting("asset:cash", "debit", money(100, "USD"))]),
      UnbalancedError,
    );
  });

  it("rejects a transaction that moves nothing", () => {
    assert.throws(
      () =>
        assertBalanced([
          posting("asset:cash", "debit", money(0, "USD")),
          posting("revenue:sales", "credit", money(0, "USD")),
        ]),
      UnbalancedError,
    );
  });

  it("rejects mixed currencies", () => {
    assert.throws(
      () =>
        assertBalanced([
          posting("asset:cash", "debit", money(100, "USD")),
          posting("revenue:sales", "credit", money(100, "EUR")),
        ]),
      ValidationError,
    );
  });

  it("accepts a balanced split across three postings", () => {
    assert.equal(
      assertBalanced([
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(60, "USD")),
        posting("liability:loan", "credit", money(40, "USD")),
      ]),
      "USD",
    );
  });
});

describe("posting", () => {
  let app: App;

  beforeEach(() => {
    app = build();
  });

  it("records a transaction", () => {
    const transaction = app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(100, "USD")),
      ],
      AT,
      "invoice 1",
    );
    assert.equal(transaction.id, "tx1");
    assert.equal(transaction.reference, "invoice 1");
    assert.equal(app.ledger.transactionCount(), 1);
  });

  it("refuses to post the same id twice", () => {
    const postings = [
      posting("asset:cash", "debit", money(100, "USD")),
      posting("revenue:sales", "credit", money(100, "USD")),
    ];
    app.ledger.post("tx1", postings, AT, null);
    assert.throws(() => app.ledger.post("tx1", postings, AT, null), ConflictError);
  });

  it("refuses a posting against an unknown account", () => {
    assert.throws(
      () =>
        app.ledger.post(
          "tx1",
          [
            posting("asset:nope", "debit", money(100, "USD")),
            posting("revenue:sales", "credit", money(100, "USD")),
          ],
          AT,
          null,
        ),
      NotFoundError,
    );
  });

  it("refuses a posting whose currency differs from the account", () => {
    app.openAccount({ id: "asset:euro", type: "asset", currency: "EUR" }, AT);
    assert.throws(
      () =>
        app.ledger.post(
          "tx1",
          [
            posting("asset:euro", "debit", money(100, "USD")),
            posting("revenue:sales", "credit", money(100, "USD")),
          ],
          AT,
          null,
        ),
      ValidationError,
    );
  });

  it("writes the journal before applying state", () => {
    const before = app.journal.size();
    app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(100, "USD")),
      ],
      AT,
      null,
    );
    assert.equal(app.journal.size(), before + 1);
  });

  it("leaves nothing behind when a transaction is rejected", () => {
    const before = app.journal.size();
    assert.throws(() =>
      app.ledger.post(
        "tx1",
        [
          posting("asset:cash", "debit", money(100, "USD")),
          posting("revenue:sales", "credit", money(99, "USD")),
        ],
        AT,
        null,
      ),
    );
    assert.equal(app.journal.size(), before);
    assert.equal(app.ledger.transactionCount(), 0);
  });
});

describe("balances", () => {
  let app: App;

  beforeEach(() => {
    app = build();
    app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(1000, "USD")),
        posting("revenue:sales", "credit", money(1000, "USD")),
      ],
      AT,
      null,
    );
  });

  it("increases an asset on the debit side", () => {
    assert.equal(app.ledger.balanceMinorUnits("asset:cash"), 1000);
  });

  it("increases revenue on the credit side", () => {
    assert.equal(app.ledger.balanceMinorUnits("revenue:sales"), 1000);
  });

  it("decreases an asset on the credit side", () => {
    app.ledger.post(
      "tx2",
      [
        posting("asset:cash", "credit", money(300, "USD")),
        posting("expense:rent", "debit", money(300, "USD")),
      ],
      AT,
      null,
    );
    assert.equal(app.ledger.balanceMinorUnits("asset:cash"), 700);
    assert.equal(app.ledger.balanceMinorUnits("expense:rent"), 300);
  });

  it("reports zero for an account with no postings", () => {
    assert.equal(app.ledger.balanceMinorUnits("liability:loan"), 0);
  });

  it("lists postings for an account", () => {
    assert.equal(app.ledger.postingsFor("asset:cash").length, 1);
  });

  it("keeps postings immutable at runtime, not only at compile time", () => {
    // `readonly` is erased by the compiler, and the callers this guarantee is
    // made to do not necessarily compile against these types at all.
    const [entry] = app.ledger.postingsFor("asset:cash");
    assert.ok(entry !== undefined);
    assert.throws(() => {
      (entry as { amount: number }).amount = 999_999;
    }, TypeError);
    assert.equal(app.ledger.balanceMinorUnits("asset:cash"), 1000);
  });
});

describe("trial balance", () => {
  it("balances after any sequence of transactions", () => {
    const app = build();
    app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(1000, "USD")),
        posting("revenue:sales", "credit", money(1000, "USD")),
      ],
      AT,
      null,
    );
    app.ledger.post(
      "tx2",
      [
        posting("expense:rent", "debit", money(250, "USD")),
        posting("asset:cash", "credit", money(250, "USD")),
      ],
      AT,
      null,
    );

    const usd = app.ledger.trialBalance()["USD"];
    assert.ok(usd !== undefined);
    assert.equal(usd.debits, 1250);
    assert.equal(usd.credits, 1250);
    assert.equal(usd.balanced, true);
  });

  it("reports each currency separately", () => {
    const app = build();
    app.openAccount({ id: "asset:euro", type: "asset", currency: "EUR" }, AT);
    app.openAccount({ id: "revenue:eu", type: "revenue", currency: "EUR" }, AT);

    app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(100, "USD")),
      ],
      AT,
      null,
    );
    app.ledger.post(
      "tx2",
      [
        posting("asset:euro", "debit", money(200, "EUR")),
        posting("revenue:eu", "credit", money(200, "EUR")),
      ],
      AT,
      null,
    );

    const totals = app.ledger.trialBalance();
    assert.equal(totals["USD"]?.debits, 100);
    assert.equal(totals["EUR"]?.debits, 200);
  });
});

describe("reversal", () => {
  it("swaps every side", () => {
    const app = build();
    app.ledger.post(
      "tx1",
      [
        posting("asset:cash", "debit", money(100, "USD")),
        posting("revenue:sales", "credit", money(100, "USD")),
      ],
      AT,
      null,
    );

    const reversal = app.ledger.reversalPostings("tx1");
    assert.equal(reversal[0]?.side, "credit");
    assert.equal(reversal[1]?.side, "debit");
  });

  it("returns the balance to zero when posted", () => {
    const app = build();
    const postings = [
      posting("asset:cash", "debit", money(100, "USD")),
      posting("revenue:sales", "credit", money(100, "USD")),
    ];
    app.ledger.post("tx1", postings, AT, null);
    app.ledger.post("tx1r", app.ledger.reversalPostings("tx1"), AT, "reversal of tx1");

    assert.equal(app.ledger.balanceMinorUnits("asset:cash"), 0);
    // History keeps both, rather than the original being edited away.
    assert.equal(app.ledger.transactionCount(), 2);
  });

  it("refuses to reverse an unknown transaction", () => {
    assert.throws(() => build().ledger.reversalPostings("nope"), NotFoundError);
  });
});
