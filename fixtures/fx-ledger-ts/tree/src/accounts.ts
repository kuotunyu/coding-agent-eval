/**
 * Accounts and their normal sides.
 *
 * Which side increases an account is not a convention that can be looked up
 * later — it decides whether a balance is right. Assets and expenses increase on
 * the debit side; liabilities, equity, and revenue increase on the credit side.
 *
 * An account's currency is fixed for its lifetime. Allowing it to change would
 * make every historical balance ambiguous about what it was denominated in.
 */

import { ConflictError, NotFoundError, ValidationError } from "./errors.js";
import type { Currency } from "./money.js";
import { isSupportedCurrency } from "./money.js";

export type AccountType = "asset" | "liability" | "equity" | "revenue" | "expense";

export type Side = "debit" | "credit";

const NORMAL_SIDE: Readonly<Record<AccountType, Side>> = {
  asset: "debit",
  expense: "debit",
  liability: "credit",
  equity: "credit",
  revenue: "credit",
};

const ACCOUNT_TYPES = Object.keys(NORMAL_SIDE) as readonly AccountType[];

/** Account ids appear in URLs and journal entries, so the legal set is small. */
const ACCOUNT_ID = /^[a-z0-9][a-z0-9:_-]{2,63}$/;

export interface Account {
  readonly id: string;
  readonly type: AccountType;
  readonly currency: Currency;
  readonly name: string;
  /** Whether the balance may fall below zero. Off by default. */
  readonly allowNegative: boolean;
  readonly openedAt: number;
}

export function normalSide(type: AccountType): Side {
  return NORMAL_SIDE[type];
}

/** Whether a posting on `side` increases an account of `type`. */
export function increasesOn(type: AccountType, side: Side): boolean {
  return normalSide(type) === side;
}

export function isAccountType(value: unknown): value is AccountType {
  return typeof value === "string" && (ACCOUNT_TYPES as readonly string[]).includes(value);
}

export function validateAccountId(id: unknown): string {
  if (typeof id !== "string" || !ACCOUNT_ID.test(id)) {
    throw new ValidationError(`account id ${String(id)} must match ${ACCOUNT_ID.source}`);
  }
  return id;
}

export interface OpenAccountRequest {
  readonly id: string;
  readonly type: string;
  readonly currency: string;
  readonly name?: string;
  readonly allowNegative?: boolean;
}

export class AccountRegistry {
  private readonly accounts = new Map<string, Account>();

  /**
   * Validate a request and build the account, without registering it.
   *
   * Separate from `register` so a caller can write the journal entry in
   * between: the record must exist before the state does, or a crash between
   * the two leaves state the journal cannot explain.
   */
  prepare(request: OpenAccountRequest, at: number): Account {
    const id = validateAccountId(request.id);
    if (!isAccountType(request.type)) {
      throw new ValidationError(
        `account type ${String(request.type)} must be one of ${ACCOUNT_TYPES.join(", ")}`,
      );
    }
    if (typeof request.currency !== "string" || !isSupportedCurrency(request.currency)) {
      throw new ValidationError(`unsupported currency ${String(request.currency)}`);
    }
    if (request.allowNegative !== undefined && typeof request.allowNegative !== "boolean") {
      throw new ValidationError("allowNegative must be a boolean");
    }
    if (this.accounts.has(id)) {
      throw new ConflictError(`account ${id} already exists`);
    }

    return {
      id,
      type: request.type,
      currency: request.currency,
      name: typeof request.name === "string" && request.name.trim() ? request.name.trim() : id,
      allowNegative: request.allowNegative ?? false,
      openedAt: at,
    };
  }

  /** Register a prepared account. */
  register(account: Account): Account {
    this.accounts.set(account.id, account);
    return account;
  }

  /** Re-add an account during journal replay, without the uniqueness check. */
  restore(account: Account): void {
    this.accounts.set(account.id, account);
  }

  get(id: string): Account {
    const account = this.accounts.get(id);
    if (account === undefined) {
      throw new NotFoundError(`no account ${id}`);
    }
    return account;
  }

  has(id: string): boolean {
    return this.accounts.has(id);
  }

  list(): readonly Account[] {
    return [...this.accounts.values()].sort((left, right) => left.id.localeCompare(right.id));
  }

  size(): number {
    return this.accounts.size;
  }

  /** Both accounts must exist and share a currency before a transfer is built. */
  requirePair(fromId: string, toId: string): { from: Account; to: Account } {
    if (fromId === toId) {
      throw new ValidationError("a transfer needs two distinct accounts");
    }
    const from = this.get(fromId);
    const to = this.get(toId);
    if (from.currency !== to.currency) {
      throw new ValidationError(
        `cannot transfer between ${from.currency} and ${to.currency}; currencies never mix`,
      );
    }
    return { from, to };
  }
}
