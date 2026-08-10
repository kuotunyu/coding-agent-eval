/**
 * Per-key async locks.
 *
 * JavaScript is single-threaded, which is often mistaken for "no races". It
 * removes torn reads, not interleaving: every `await` is a point where another
 * task can run. A balance read before an await and a write after it are as
 * racy here as they would be with threads.
 *
 * So a transfer holds a lock across its whole check-then-write, and locks are
 * taken in a deterministic order so two transfers touching the same pair of
 * accounts cannot each hold what the other needs.
 */

export type Release = () => void;

export class LockManager {
  /** The tail of each key's queue. Awaiting it means waiting for everyone ahead. */
  private readonly tails = new Map<string, Promise<void>>();
  private held = 0;

  /** Acquire one key. Resolves when the caller owns it. */
  async acquire(key: string): Promise<Release> {
    const previous = this.tails.get(key) ?? Promise.resolve();

    // Releasing twice must not decrement the count twice. A caller that
    // released in both a success path and a `finally` would otherwise drive the
    // count negative, and the count is what tests assert nothing is leaked.
    let released = false;
    let release!: Release;
    const mine = new Promise<void>((resolve) => {
      release = () => {
        if (released) return;
        released = true;
        this.held -= 1;
        resolve();
      };
    });

    // Queue behind whoever holds it, and become the new tail before awaiting,
    // so a caller arriving during the await queues behind this one rather than
    // behind the previous holder.
    this.tails.set(key, previous.then(() => mine));

    await previous;
    this.held += 1;
    return release;
  }

  /**
   * Acquire several keys, in sorted order.
   *
   * The order is what prevents deadlock. Two transfers between the same pair of
   * accounts in opposite directions would otherwise each take one lock and wait
   * forever for the other.
   */
  async acquireAll(keys: readonly string[]): Promise<Release> {
    const ordered = [...new Set(keys)].sort();
    const releases: Release[] = [];

    for (const key of ordered) {
      releases.push(await this.acquire(key));
    }

    // Released in reverse, so a waiter sees keys freed in the opposite order to
    // acquisition. Reversed into a new array rather than in place: `reverse`
    // mutates, so a second call would undo the order of the first.
    const inReleaseOrder = [...releases].reverse();
    return () => {
      for (const release of inReleaseOrder) {
        release();
      }
    };
  }

  /** Run `work` while holding `keys`, releasing even if it throws. */
  async withLocks<T>(keys: readonly string[], work: () => Promise<T> | T): Promise<T> {
    const release = await this.acquireAll(keys);
    try {
      return await work();
    } finally {
      release();
    }
  }

  heldCount(): number {
    return this.held;
  }

  trackedKeys(): number {
    return this.tails.size;
  }
}
