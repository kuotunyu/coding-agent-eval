/**
 * Double-entry posting and balances.
 *
 * Every transaction balances: total debits equal total credits. A transaction
 * that does not is rejected before anything is written, because a ledger that
 * accepts one has no way to tell later which side was wrong.
 *
 * Postings are immutable. A mistake is corrected by a reversing transaction, so
 * the history says what happened and then what was done about it, rather than
 * quietly saying only the latter.
 */

import type { Account, AccountType, Side } from "./accounts.js";
import { AccountRegistry, increasesOn } from "./accounts.js";
import { ConflictError, NotFoundError, UnbalancedError, ValidationError } from "./errors.js";
import type { Journal } from "./journal.js";
import type { Currency, Money } from "./money.js";
import { add, compare, money, subtract, zero } from "./money.js";

export interface Posting {
  readonly accountId: string;
  readonly side: Side;
  readonly amount: number;
  readonly currency: Currency;
}

export interface Transaction {
  readonly id: string;
  readonly at: number;
  readonly reference: string | null;
  readonly postings: readonly Posting[];
}

export function posting(accountId: string, side: Side, value: Money): Posting {
  if (side !== "debit" && side !== "credit") {
    throw new ValidationError(`posting side must be debit or credit, got ${String(side)}`);
  }
  // Not frozen here. `apply` is the single point every posting passes through,
  // including the ones replay parses out of JSON and the ones
  // `reversalPostings` builds, so freezing there covers all of them and this
  // would only be a second place to keep in step.
  return { accountId, side, amount: value.amount, currency: value.currency };
}

function totalFor(postings: readonly Posting[], side: Side, currency: Currency): Money {
  let total = zero(currency);
  for (const entry of postings) {
    if (entry.side === side) {
      total = add(total, money(entry.amount, entry.currency));
    }
  }
  return total;
}

/**
 * Reject anything that is not a well-formed, balanced transaction.
 *
 * Called before the journal is touched. Validating after writing would leave a
 * record of a transaction the ledger then refused, which is worse than either
 * outcome alone.
 */
export function assertBalanced(postings: readonly Posting[]): Currency {
  if (postings.length < 2) {
    throw new UnbalancedError("a transaction needs at least two postings");
  }

  const first = postings[0];
  if (first === undefined) {
    throw new UnbalancedError("a transaction needs at least two postings");
  }

  const currency = first.currency;
  for (const entry of postings) {
    if (entry.currency !== currency) {
      throw new ValidationError(
        `transaction mixes ${currency} and ${entry.currency}; currencies never mix`,
      );
    }
    money(entry.amount, entry.currency);
  }

  const debits = totalFor(postings, "debit", currency);
  const credits = totalFor(postings, "credit", currency);
  if (compare(debits, credits) !== 0) {
    throw new UnbalancedError(
      `transaction does not balance: debits ${debits.amount}, credits ${credits.amount}`,
    );
  }
  if (debits.amount === 0) {
    throw new UnbalancedError("a transaction must move a non-zero amount");
  }
  return currency;
}

export class Ledger {
  private readonly transactions = new Map<string, Transaction>();
  private readonly postingsByAccount = new Map<string, Posting[]>();

  constructor(
    readonly accounts: AccountRegistry,
    private readonly journal: Journal,
  ) {}

  /**
   * Post a balanced transaction.
   *
   * The journal is appended first. If the process dies between the two, replay
   * reproduces the transaction; the reverse order would lose it.
   */
  post(id: string, postings: readonly Posting[], at: number, reference: string | null): Transaction {
    if (this.transactions.has(id)) {
      throw new ConflictError(`transaction ${id} already posted`);
    }
    assertBalanced(postings);
    for (const entry of postings) {
      const account = this.accounts.get(entry.accountId);
      if (account.currency !== entry.currency) {
        throw new ValidationError(
          `account ${account.id} holds ${account.currency}, posting is ${entry.currency}`,
        );
      }
    }

    this.journal.append({
      kind: "transaction_posted",
      at,
      transactionId: id,
      reference,
      postings: [...postings],
    });

    this.apply({ id, at, reference, postings: [...postings] });
    return this.require(id);
  }

  /**
   * Apply a transaction to in-memory state. Used by `post` and by replay.
   *
   * Postings arriving from replay were parsed out of JSON and never went
   * through `posting`, so they are frozen here too.
   */
  apply(transaction: Transaction): void {
    this.transactions.set(transaction.id, transaction);
    for (const entry of transaction.postings) {
      Object.freeze(entry);
      const existing = this.postingsByAccount.get(entry.accountId);
      if (existing === undefined) {
        this.postingsByAccount.set(entry.accountId, [entry]);
      } else {
        existing.push(entry);
      }
    }
  }

  require(id: string): Transaction {
    const transaction = this.transactions.get(id);
    if (transaction === undefined) {
      throw new NotFoundError(`no transaction ${id}`);
    }
    return transaction;
  }

  has(id: string): boolean {
    return this.transactions.has(id);
  }

  transactionCount(): number {
    return this.transactions.size;
  }

  /**
   * An account's balance in minor units.
   *
   * Postings on the account's normal side add; the other side subtracts. The
   * result may be negative for an account that allows it, so this returns a
   * signed integer rather than a Money, which cannot be negative by design.
   */
  balanceMinorUnits(accountId: string): number {
    const account = this.accounts.get(accountId);
    const postings = this.postingsByAccount.get(accountId) ?? [];

    let total = 0;
    for (const entry of postings) {
      total += increasesOn(account.type, entry.side) ? entry.amount : -entry.amount;
    }
    return total;
  }

  balance(accountId: string): { account: Account; minorUnits: number } {
    return { account: this.accounts.get(accountId), minorUnits: this.balanceMinorUnits(accountId) };
  }

  postingsFor(accountId: string): readonly Posting[] {
    return [...(this.postingsByAccount.get(accountId) ?? [])];
  }

  /**
   * Whether taking `amount` from `accountId` is permitted.
   *
   * Callers must hold the account's lock across this check and the write it
   * guards, or two of them can both observe sufficient funds.
   */
  canDebit(accountId: string, amount: number): boolean {
    const account = this.accounts.get(accountId);
    if (account.allowNegative) {
      return true;
    }
    return this.balanceMinorUnits(accountId) - amount >= 0;
  }

  /** Debits and credits by currency. They must be equal for every currency. */
  trialBalance(): Record<Currency, { debits: number; credits: number; balanced: boolean }> {
    const totals: Record<string, { debits: number; credits: number; balanced: boolean }> = {};

    for (const transaction of this.transactions.values()) {
      for (const entry of transaction.postings) {
        const bucket = (totals[entry.currency] ??= { debits: 0, credits: 0, balanced: true });
        if (entry.side === "debit") {
          bucket.debits += entry.amount;
        } else {
          bucket.credits += entry.amount;
        }
      }
    }

    for (const bucket of Object.values(totals)) {
      bucket.balanced = bucket.debits === bucket.credits;
    }
    return totals;
  }

  /** Build the reversal of an existing transaction, sides swapped. */
  reversalPostings(transactionId: string): readonly Posting[] {
    const original = this.require(transactionId);
    return original.postings.map((entry) => ({
      accountId: entry.accountId,
      side: entry.side === "debit" ? ("credit" as const) : ("debit" as const),
      amount: entry.amount,
      currency: entry.currency,
    }));
  }
}

export type { AccountType, Side };
export { subtract };
