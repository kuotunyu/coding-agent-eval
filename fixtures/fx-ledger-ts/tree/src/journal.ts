/**
 * The append-only journal.
 *
 * Entries are written before in-memory state changes, so a crash can leave the
 * record ahead of the state but never behind it. Replaying a journal that got
 * ahead reproduces the intended state; replaying one that fell behind would
 * silently lose whatever was only ever in memory.
 *
 * Nothing here is ever rewritten. A mistake is corrected by appending a
 * reversing entry, which is the whole reason a ledger keeps a journal rather
 * than a table of current values.
 */

import { appendFileSync, existsSync, readFileSync } from "node:fs";

import { ValidationError } from "./errors.js";
import type { Account } from "./accounts.js";
import type { Posting } from "./ledger.js";
import type { Transfer } from "./transfers.js";

export type JournalEntry =
  | { readonly kind: "account_opened"; readonly seq: number; readonly at: number; readonly account: Account }
  | {
      readonly kind: "transaction_posted";
      readonly seq: number;
      readonly at: number;
      readonly transactionId: string;
      readonly reference: string | null;
      readonly postings: readonly Posting[];
    }
  | {
      // The whole transfer, not a reference to one. A transfer is state in its
      // own right, not a view over its transaction: the transaction says money
      // moved, the transfer says whose instruction moved it and whether that
      // instruction has cleared. Replay cannot reconstruct the second from the
      // first, so it is recorded.
      readonly kind: "transfer_created";
      readonly seq: number;
      readonly at: number;
      readonly transfer: Transfer;
    }
  | {
      readonly kind: "transfer_settled";
      readonly seq: number;
      readonly at: number;
      readonly transferId: string;
      readonly batchId: string;
    };

/**
 * A journal entry before it is stamped with a sequence number.
 *
 * Distributive on purpose. A plain `Omit<JournalEntry, "seq">` over a
 * discriminated union collapses to the keys every member shares, which here is
 * just `kind` and `at` — so every payload field would be rejected.
 */
export type DraftEntry = JournalEntry extends infer Member
  ? Member extends JournalEntry
    ? Omit<Member, "seq">
    : never
  : never;

export interface Journal {
  append(entry: DraftEntry): JournalEntry;
  read(): readonly JournalEntry[];
  size(): number;
}

/** A journal held in memory. Used by tests and by replay. */
export class MemoryJournal implements Journal {
  private readonly entries: JournalEntry[] = [];

  append(entry: DraftEntry): JournalEntry {
    const stamped = { ...entry, seq: this.entries.length + 1 } as JournalEntry;
    this.entries.push(stamped);
    return stamped;
  }

  read(): readonly JournalEntry[] {
    return [...this.entries];
  }

  size(): number {
    return this.entries.length;
  }
}

/**
 * A journal backed by a JSONL file.
 *
 * Writes are synchronous and unbuffered. An async append would return before
 * the bytes reached the file, so the caller could update state believing the
 * record was durable when it was still in a buffer.
 */
export class FileJournal implements Journal {
  private nextSeq: number;

  constructor(private readonly path: string) {
    this.nextSeq = this.read().length + 1;
  }

  append(entry: DraftEntry): JournalEntry {
    const stamped = { ...entry, seq: this.nextSeq } as JournalEntry;
    appendFileSync(this.path, `${JSON.stringify(stamped)}\n`, { encoding: "utf8" });
    this.nextSeq += 1;
    return stamped;
  }

  read(): readonly JournalEntry[] {
    if (!existsSync(this.path)) {
      return [];
    }
    const text = readFileSync(this.path, { encoding: "utf8" });
    const entries: JournalEntry[] = [];

    for (const [index, line] of text.split("\n").entries()) {
      if (line.trim() === "") continue;
      try {
        entries.push(JSON.parse(line) as JournalEntry);
      } catch (cause) {
        throw new ValidationError(
          `journal line ${index + 1} is not valid JSON: ${String(cause)}`,
        );
      }
    }
    return entries;
  }

  size(): number {
    return this.read().length;
  }
}

/**
 * Check that a journal is a well-formed history.
 *
 * Sequence numbers must start at one and increase by one. A gap means an entry
 * was lost, and replaying across it would produce a state that never existed.
 */
export function validateSequence(entries: readonly JournalEntry[]): void {
  for (const [index, entry] of entries.entries()) {
    const expected = index + 1;
    if (entry.seq !== expected) {
      throw new ValidationError(
        `journal is not contiguous: entry ${index + 1} has seq ${entry.seq}, expected ${expected}`,
      );
    }
  }
}
