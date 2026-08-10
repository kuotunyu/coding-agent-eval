/**
 * Bearer-token authentication for the privileged routes.
 *
 * Comparison is constant time. `===` on strings stops at the first differing
 * byte, so how long a rejection takes tells the caller how much of their guess
 * was right, and a token can be recovered a byte at a time.
 *
 * A missing token and a wrong token produce the same answer. Distinguishing
 * them tells an unauthenticated caller whether authentication is configured,
 * which is only useful for probing.
 */

// Called through the namespace rather than as a named import, so a test can
// observe that the comparison really goes through it.
import crypto from "node:crypto";

import { UnauthorizedError } from "./errors.js";

const BEARER_PREFIX = "Bearer ";

/** One message for every authentication failure, whatever the cause. */
const FAILURE_MESSAGE = "authentication required";

export function extractBearerToken(headerValue: string | undefined | null): string | null {
  if (typeof headerValue !== "string" || !headerValue.startsWith(BEARER_PREFIX)) {
    return null;
  }
  const token = headerValue.slice(BEARER_PREFIX.length).trim();
  return token === "" ? null : token;
}

/**
 * Compare in constant time.
 *
 * `timingSafeEqual` throws on differing lengths, which would itself leak the
 * length, so both sides are hashed to a fixed width first.
 */
function constantTimeEquals(presented: string, expected: string): boolean {
  const left = Buffer.from(presented, "utf8");
  const right = Buffer.from(expected, "utf8");

  if (left.length !== right.length) {
    // Still compare, so the rejection takes the same shape as a length match.
    const padded = Buffer.alloc(Math.max(left.length, right.length));
    const other = Buffer.alloc(padded.length);
    left.copy(padded);
    right.copy(other);
    crypto.timingSafeEqual(padded, other);
    return false;
  }
  return crypto.timingSafeEqual(left, right);
}

/**
 * Throw unless `presented` matches `expected`.
 *
 * An unconfigured token closes the privileged routes rather than opening them.
 * An unset secret is a deployment that has not finished, not one that trusts
 * everyone.
 */
export function verifyToken(presented: string | null, expected: string | null): void {
  if (expected === null || expected === "") {
    throw new UnauthorizedError(FAILURE_MESSAGE);
  }
  if (presented === null) {
    throw new UnauthorizedError(FAILURE_MESSAGE);
  }
  if (!constantTimeEquals(presented, expected)) {
    throw new UnauthorizedError(FAILURE_MESSAGE);
  }
}

export function authenticate(
  headerValue: string | undefined | null,
  expected: string | null,
): void {
  verifyToken(extractBearerToken(headerValue), expected);
}
