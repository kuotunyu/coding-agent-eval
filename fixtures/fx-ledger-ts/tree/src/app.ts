/**
 * Wiring, and replay from the journal.
 *
 * Replay is the property that makes the journal worth keeping: state is a
 * function of the record, so a process that dies mid-write comes back to a
 * state the record explains rather than to whatever happened to be in memory.
 */

import type { Account, OpenAccountRequest } from "./accounts.js";
import { AccountRegistry } from "./accounts.js";
import { Api } from "./api.js";
import type { ApiConfig } from "./api.js";
import { IdempotencyStore } from "./idempotency.js";
import type { Journal, JournalEntry } from "./journal.js";
import { MemoryJournal, validateSequence } from "./journal.js";
import { Ledger } from "./ledger.js";
import { LockManager } from "./locks.js";
import { SettlementService } from "./settlement.js";
import { TransferService } from "./transfers.js";

export interface AppOptions {
  readonly journal?: Journal;
  readonly adminToken?: string | null;
  readonly maxBodyBytes?: number;
  readonly clock?: () => number;
}

export const DEFAULT_MAX_BODY_BYTES = 64 * 1024;

export class App {
  readonly accounts: AccountRegistry;
  readonly ledger: Ledger;
  readonly locks: LockManager;
  readonly idempotency: IdempotencyStore;
  readonly transfers: TransferService;
  readonly settlement: SettlementService;
  readonly api: Api;
  readonly journal: Journal;

  constructor(options: AppOptions = {}) {
    this.journal = options.journal ?? new MemoryJournal();
    this.accounts = new AccountRegistry();
    this.ledger = new Ledger(this.accounts, this.journal);
    this.locks = new LockManager();
    this.idempotency = new IdempotencyStore();
    this.transfers = new TransferService(
      this.ledger,
      this.accounts,
      this.locks,
      this.idempotency,
      this.journal,
    );
    this.settlement = new SettlementService(this.transfers, this.locks);

    const config: ApiConfig = {
      adminToken: options.adminToken ?? null,
      maxBodyBytes: options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES,
    };
    this.api = new Api(
      this.accounts,
      this.ledger,
      this.transfers,
      this.settlement,
      config,
      options.clock ?? (() => Date.now()),
      (request, at) => this.openAccount(request, at),
    );
  }

  /**
   * Open an account: validate, journal, then register.
   *
   * That order is the write-ahead invariant. Registering first would let a
   * crash leave an account in memory that the journal never mentions, and
   * replay would then silently drop it.
   */
  openAccount(request: OpenAccountRequest, at: number): Account {
    const account = this.accounts.prepare(request, at);
    this.journal.append({ kind: "account_opened", at, account });
    return this.accounts.register(account);
  }

  /**
   * Rebuild in-memory state from a journal.
   *
   * The sequence is checked first: a gap means an entry was lost, and replaying
   * across one would produce a state that never existed, which is worse than
   * refusing to start.
   *
   * Nothing here writes to the journal. Replay reads the record; an append
   * during it would grow the file every time the process started.
   */
  replay(entries: readonly JournalEntry[]): void {
    validateSequence(entries);

    for (const entry of entries) {
      switch (entry.kind) {
        case "account_opened":
          this.accounts.restore(entry.account);
          break;
        case "transaction_posted":
          this.ledger.apply({
            id: entry.transactionId,
            at: entry.at,
            reference: entry.reference,
            postings: entry.postings,
          });
          break;
        case "transfer_created":
          this.transfers.restore(entry.transfer);
          break;
        case "transfer_settled": {
          // Applied over the created entry rather than replacing it, so the
          // fields settlement does not touch survive. `get` throws if the
          // created entry is missing, which is a journal that cannot be
          // replayed rather than one to be replayed approximately.
          const transfer = this.transfers.get(entry.transferId);
          this.transfers.restore({ ...transfer, state: "settled", settledAt: entry.at });
          break;
        }
      }
    }
  }
}

/** Build an app and replay `journal` into it. */
export function restore(journal: Journal, options: Omit<AppOptions, "journal"> = {}): App {
  const app = new App({ ...options, journal });
  app.replay(journal.read());
  return app;
}
