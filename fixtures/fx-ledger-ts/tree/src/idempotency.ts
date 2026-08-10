/**
 * Idempotency keys for transfers.
 *
 * A client retrying after a timeout cannot know whether the first attempt
 * landed. Without a key it either drops the transfer or makes it twice, and for
 * money the second is the worse failure.
 *
 * Keys are global rather than per-account. A transfer is not owned by either
 * side, so scoping a key to one of them would let the same key mean two
 * different things depending on which account was consulted.
 */

import { ValidationError } from "./errors.js";

export const KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000;

export interface KeyRecord {
  readonly key: string;
  readonly transferId: string;
  readonly createdAt: number;
  readonly expiresAt: number;
}

export function validateKey(key: unknown): string {
  if (typeof key !== "string" || !KEY_PATTERN.test(key)) {
    throw new ValidationError(`idempotency key must match ${KEY_PATTERN.source}`);
  }
  return key;
}

export class IdempotencyStore {
  private readonly records = new Map<string, KeyRecord>();

  constructor(private readonly ttlMs: number = DEFAULT_TTL_MS) {}

  /**
   * The live record for a key, or undefined.
   *
   * An expired record reads as absent rather than being returned with a flag,
   * so a caller who forgot to check the expiry cannot resurrect a transfer id
   * from arbitrarily long ago.
   */
  lookup(key: string, at: number): KeyRecord | undefined {
    const validated = validateKey(key);
    const record = this.records.get(validated);
    if (record === undefined || record.expiresAt <= at) {
      return undefined;
    }
    return record;
  }

  remember(key: string, transferId: string, at: number): KeyRecord {
    const validated = validateKey(key);
    const record: KeyRecord = {
      key: validated,
      transferId,
      createdAt: at,
      expiresAt: at + this.ttlMs,
    };
    this.records.set(validated, record);
    return record;
  }

  purgeExpired(at: number): number {
    let removed = 0;
    for (const [key, record] of this.records) {
      if (record.expiresAt <= at) {
        this.records.delete(key);
        removed += 1;
      }
    }
    return removed;
  }

  size(): number {
    return this.records.size;
  }
}
