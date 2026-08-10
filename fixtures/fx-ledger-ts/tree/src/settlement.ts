/**
 * Batch settlement.
 *
 * A batch is all-or-nothing. Settling part of one and stopping would leave the
 * caller unable to tell which transfers went through without inspecting each,
 * and a retry would then double-settle whatever had already succeeded.
 *
 * The batch is validated in full before anything is marked, so the common
 * failures — a transfer already settled, one that vanished — are caught while
 * nothing has changed yet.
 */

import { ConflictError, ValidationError } from "./errors.js";
import { LockManager } from "./locks.js";
import type { Transfer } from "./transfers.js";
import { TransferService } from "./transfers.js";

export interface BatchResult {
  readonly batchId: string;
  readonly settled: readonly Transfer[];
  readonly at: number;
}

export const DEFAULT_BATCH_SIZE = 50;
export const MAX_BATCH_SIZE = 500;

export class SettlementService {
  /** Per service, for the same reason the transfer counter is. */
  private batchCounter = 0;

  constructor(
    private readonly transfers: TransferService,
    private readonly locks: LockManager,
  ) {}

  private nextBatchId(at: number): string {
    this.batchCounter += 1;
    return `batch_${at.toString(36)}_${this.batchCounter.toString(36).padStart(3, "0")}`;
  }

  /**
   * Settle up to `batchSize` pending transfers.
   *
   * Every account the batch touches is locked for its duration, so a transfer
   * cannot be created against one of them part way through and end up settled
   * by a batch that never inspected it.
   */
  async run(at: number, batchSize: number = DEFAULT_BATCH_SIZE): Promise<BatchResult> {
    if (!Number.isSafeInteger(batchSize) || batchSize < 1 || batchSize > MAX_BATCH_SIZE) {
      throw new ValidationError(`batchSize must be an integer between 1 and ${MAX_BATCH_SIZE}`);
    }

    const candidates = this.transfers.pending().slice(0, batchSize);
    if (candidates.length === 0) {
      return { batchId: this.nextBatchId(at), settled: [], at };
    }

    const accountIds = candidates.flatMap((transfer) => [
      transfer.fromAccountId,
      transfer.toAccountId,
    ]);

    return this.locks.withLocks(accountIds, () => {
      const batchId = this.nextBatchId(at);

      // Re-read and check every candidate before marking any. A transfer that
      // changed state while the batch was being assembled fails here, with
      // nothing yet applied.
      for (const candidate of candidates) {
        const current = this.transfers.get(candidate.id);
        if (current.state !== "pending") {
          throw new ConflictError(
            `transfer ${candidate.id} became ${current.state} before the batch ran`,
          );
        }
      }

      const settled = candidates.map((candidate) =>
        this.transfers.markSettled(candidate.id, batchId, at),
      );
      return { batchId, settled, at };
    });
  }

  /** Settle everything pending, one batch at a time. Returns each batch. */
  async runUntilDrained(
    at: number,
    batchSize: number = DEFAULT_BATCH_SIZE,
  ): Promise<readonly BatchResult[]> {
    const results: BatchResult[] = [];

    // Bounded rather than `while (true)`: a bug that stopped draining the queue
    // would otherwise hang the process instead of failing.
    const maxBatches = Math.ceil(MAX_BATCH_SIZE / Math.max(1, batchSize)) + 1;
    for (let index = 0; index < maxBatches; index += 1) {
      const result = await this.run(at, batchSize);
      if (result.settled.length === 0) {
        break;
      }
      results.push(result);
    }
    return results;
  }
}
