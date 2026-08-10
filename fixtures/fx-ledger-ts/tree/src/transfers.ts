/**
 * Transfers between two accounts.
 *
 * A transfer is one balanced transaction: a credit on the source and a debit on
 * the destination, or the reverse, depending on which side each account
 * increases.
 *
 * The sufficiency check and the posting happen under the same locks. Checking
 * outside them is the classic overdraw: two transfers each read a balance of
 * 100, each decide 60 is affordable, and the account ends at -20 with both
 * having been told they succeeded.
 */

import { AccountRegistry } from "./accounts.js";
import { ConflictError, InsufficientFundsError, NotFoundError, ValidationError } from "./errors.js";
import { IdempotencyStore } from "./idempotency.js";
import type { Journal } from "./journal.js";
import { Ledger, posting } from "./ledger.js";
import { LockManager } from "./locks.js";
import type { Money } from "./money.js";
import { money } from "./money.js";

export type TransferState = "pending" | "settled" | "reversed";

export interface Transfer {
  readonly id: string;
  readonly fromAccountId: string;
  readonly toAccountId: string;
  readonly amount: number;
  readonly currency: string;
  readonly reference: string | null;
  readonly state: TransferState;
  readonly createdAt: number;
  readonly settledAt: number | null;
  readonly transactionId: string;
}

export interface TransferRequest {
  readonly fromAccountId: string;
  readonly toAccountId: string;
  readonly amount: number;
  readonly currency: string;
  readonly reference?: string;
  readonly idempotencyKey?: string;
}

/**
 * `tr_<created-at base36>_<ordinal base36>`.
 *
 * A transfer and its transaction share an ordinal, so the pair is visibly
 * linked and one counter covers both. The ordinal is zero-padded so sorting by
 * id orders by creation, which is what makes it a usable tie-break between
 * transfers created in the same millisecond.
 */
function idFor(prefix: string, at: number, ordinal: number): string {
  return `${prefix}_${at.toString(36)}_${ordinal.toString(36).padStart(4, "0")}`;
}

/**
 * Recover the ordinal from an id built by `idFor`, or 0.
 *
 * Only used to carry the counter across a replay. Parsing an id is normally a
 * smell, but the format is defined immediately above and never leaves this
 * module, so the two cannot drift apart.
 */
function ordinalOf(id: string): number {
  const ordinal = Number.parseInt(id.split("_").at(-1) ?? "", 36);
  return Number.isSafeInteger(ordinal) && ordinal > 0 ? ordinal : 0;
}

export class TransferService {
  private readonly transfers = new Map<string, Transfer>();

  /**
   * Per service, not per module.
   *
   * A module-level counter restarts at zero when the process does, so a
   * restored service would mint ids colliding with the ones it had just
   * replayed. Holding it here lets `restore` carry it forward.
   */
  private counter = 0;

  constructor(
    private readonly ledger: Ledger,
    private readonly accounts: AccountRegistry,
    private readonly locks: LockManager,
    private readonly idempotency: IdempotencyStore,
    private readonly journal: Journal,
  ) {}

  /**
   * Create and post a transfer.
   *
   * Validation runs before the locks, so a malformed request does not queue
   * behind live work. Everything that reads or writes balances runs inside
   * them.
   */
  async transfer(request: TransferRequest, at: number): Promise<Transfer> {
    const value = this.validate(request);
    const { from, to } = this.accounts.requirePair(
      request.fromAccountId,
      request.toAccountId,
    );

    if (request.idempotencyKey !== undefined) {
      const existing = this.idempotency.lookup(request.idempotencyKey, at);
      if (existing !== undefined) {
        const previous = this.transfers.get(existing.transferId);
        if (previous !== undefined) {
          return previous;
        }
      }
    }

    return this.locks.withLocks([from.id, to.id], () => {
      // Inside the locks: the balance cannot move between this check and the
      // post below it.
      if (!this.ledger.canDebit(from.id, value.amount)) {
        throw new InsufficientFundsError(
          `account ${from.id} cannot cover ${value.amount} ${value.currency}`,
        );
      }

      this.counter += 1;
      const id = idFor("tr", at, this.counter);
      const transactionId = idFor("tx", at, this.counter);
      const reference = request.reference ?? null;

      this.ledger.post(
        transactionId,
        [posting(from.id, "credit", value), posting(to.id, "debit", value)],
        at,
        reference,
      );

      const transfer: Transfer = {
        id,
        fromAccountId: from.id,
        toAccountId: to.id,
        amount: value.amount,
        currency: value.currency,
        reference,
        state: "pending",
        createdAt: at,
        settledAt: null,
        transactionId,
      };

      // Journalled before it exists in memory, same as every other state
      // change here. Without this entry a restarted process would have the
      // money moved and no record of whose transfer moved it.
      this.journal.append({ kind: "transfer_created", at, transfer });
      this.transfers.set(id, transfer);

      if (request.idempotencyKey !== undefined) {
        this.idempotency.remember(request.idempotencyKey, id, at);
      }
      return transfer;
    });
  }

  private validate(request: TransferRequest): Money {
    if (request === null || typeof request !== "object") {
      throw new ValidationError("transfer request must be an object");
    }
    if (request.reference !== undefined && typeof request.reference !== "string") {
      throw new ValidationError("reference must be a string");
    }
    if (request.reference !== undefined && request.reference.length > 256) {
      throw new ValidationError("reference must be 256 characters or fewer");
    }
    const value = money(request.amount, request.currency);
    if (value.amount === 0) {
      throw new ValidationError("a transfer must move a non-zero amount");
    }
    return value;
  }

  get(id: string): Transfer {
    const transfer = this.transfers.get(id);
    if (transfer === undefined) {
      throw new NotFoundError(`no transfer ${id}`);
    }
    return transfer;
  }

  pending(): readonly Transfer[] {
    return [...this.transfers.values()]
      .filter((transfer) => transfer.state === "pending")
      .sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id));
  }

  count(): number {
    return this.transfers.size;
  }

  /**
   * Mark a transfer settled.
   *
   * Refuses a transfer that is already settled, so a batch replayed after a
   * partial failure cannot settle the same transfer twice.
   */
  markSettled(id: string, batchId: string, at: number): Transfer {
    const transfer = this.get(id);
    if (transfer.state !== "pending") {
      throw new ConflictError(`transfer ${id} is ${transfer.state}, not pending`);
    }

    this.journal.append({ kind: "transfer_settled", at, transferId: id, batchId });

    const settled: Transfer = { ...transfer, state: "settled", settledAt: at };
    this.transfers.set(id, settled);
    return settled;
  }

  /**
   * Restore a transfer during replay, bypassing state checks.
   *
   * Writes nothing to the journal: replay reads the record, it does not add to
   * it. The counter is carried past the restored id so the next transfer this
   * service mints cannot collide with one it has just replayed.
   */
  restore(transfer: Transfer): void {
    this.transfers.set(transfer.id, transfer);
    this.counter = Math.max(this.counter, ordinalOf(transfer.id));
  }
}
