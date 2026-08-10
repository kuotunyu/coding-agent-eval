/**
 * Error types.
 *
 * Each carries the HTTP status it should produce, so the API layer never maps
 * error classes to status codes and cannot drift out of step with them.
 */

export class LedgerError extends Error {
  readonly status: number = 500;
  readonly code: string = "internal_error";

  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class ValidationError extends LedgerError {
  override readonly status = 400;
  override readonly code = "invalid_request";
}

export class NotFoundError extends LedgerError {
  override readonly status = 404;
  override readonly code = "not_found";
}

/**
 * Authentication failed.
 *
 * The message is identical for a missing token and a wrong one. A different
 * answer would tell an unauthenticated caller which of the two happened, which
 * is information they can only use to probe.
 */
export class UnauthorizedError extends LedgerError {
  override readonly status = 401;
  override readonly code = "unauthorized";
}

export class ConflictError extends LedgerError {
  override readonly status = 409;
  override readonly code = "conflict";
}

/** The ledger would be left in an inconsistent state, so nothing was written. */
export class UnbalancedError extends LedgerError {
  override readonly status = 422;
  override readonly code = "unbalanced";
}

export class InsufficientFundsError extends LedgerError {
  override readonly status = 422;
  override readonly code = "insufficient_funds";
}

export function isLedgerError(value: unknown): value is LedgerError {
  return value instanceof LedgerError;
}
