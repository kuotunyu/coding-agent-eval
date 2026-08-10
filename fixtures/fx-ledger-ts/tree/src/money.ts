/**
 * Money as integer minor units.
 *
 * Never floating point. A ledger that adds 0.1 + 0.2 and stores
 * 0.30000000000000004 has lost the property it exists to provide, and the loss
 * is silent until someone reconciles.
 *
 * Amounts are non-negative. Direction is which side of a posting an account
 * sits on, not the sign of the number: a negative debit and a positive credit
 * would be two ways to say one thing, and code would eventually disagree about
 * which it meant.
 */

import { ValidationError } from "./errors.js";

/** Minor units per major unit, by ISO 4217 code. */
const EXPONENTS: Readonly<Record<string, number>> = {
  USD: 2,
  EUR: 2,
  GBP: 2,
  JPY: 0,
  KRW: 0,
  TWD: 2,
  CHF: 2,
};

export type Currency = string;

export interface Money {
  /** Non-negative integer count of minor units. */
  readonly amount: number;
  readonly currency: Currency;
}

export function isSupportedCurrency(currency: string): boolean {
  return Object.prototype.hasOwnProperty.call(EXPONENTS, currency);
}

export function supportedCurrencies(): readonly string[] {
  return Object.keys(EXPONENTS).sort();
}

export function minorUnitExponent(currency: Currency): number {
  const exponent = EXPONENTS[currency];
  if (exponent === undefined) {
    throw new ValidationError(`unsupported currency ${currency}`);
  }
  return exponent;
}

/**
 * Build a Money, rejecting anything that is not a non-negative safe integer.
 *
 * `Number.isSafeInteger` rather than `Number.isInteger`: beyond 2^53 addition
 * silently stops being exact, and a balance that stops being exact without
 * saying so is the failure this module exists to prevent.
 */
export function money(amount: number, currency: Currency): Money {
  if (typeof amount !== "number" || !Number.isSafeInteger(amount)) {
    throw new ValidationError(
      `amount must be a safe integer number of minor units, got ${String(amount)}`,
    );
  }
  if (amount < 0) {
    throw new ValidationError("amount must not be negative; direction is the posting side");
  }
  if (typeof currency !== "string" || !isSupportedCurrency(currency)) {
    throw new ValidationError(`unsupported currency ${String(currency)}`);
  }
  return { amount, currency };
}

export function zero(currency: Currency): Money {
  return money(0, currency);
}

function requireSameCurrency(left: Money, right: Money): void {
  if (left.currency !== right.currency) {
    throw new ValidationError(
      `cannot combine ${left.currency} and ${right.currency}; currencies never mix`,
    );
  }
}

export function add(left: Money, right: Money): Money {
  requireSameCurrency(left, right);
  return money(left.amount + right.amount, left.currency);
}

/** Subtract, refusing to produce a negative Money. Callers compare first. */
export function subtract(left: Money, right: Money): Money {
  requireSameCurrency(left, right);
  if (right.amount > left.amount) {
    throw new ValidationError("subtraction would produce a negative amount");
  }
  return money(left.amount - right.amount, left.currency);
}

export function compare(left: Money, right: Money): number {
  requireSameCurrency(left, right);
  if (left.amount < right.amount) return -1;
  if (left.amount > right.amount) return 1;
  return 0;
}

export function isZero(value: Money): boolean {
  return value.amount === 0;
}

/**
 * Sum a list. An empty list needs a currency, since there is nothing to infer
 * one from and guessing would produce a total in the wrong denomination.
 */
export function sum(values: readonly Money[], currency: Currency): Money {
  let total = zero(currency);
  for (const value of values) {
    total = add(total, value);
  }
  return total;
}

/** Format for display only. Never parse this back; it is lossy by design. */
export function format(value: Money): string {
  const exponent = minorUnitExponent(value.currency);
  if (exponent === 0) {
    return `${value.amount} ${value.currency}`;
  }
  const divisor = 10 ** exponent;
  const major = Math.floor(value.amount / divisor);
  const minor = value.amount % divisor;
  return `${major}.${String(minor).padStart(exponent, "0")} ${value.currency}`;
}

/**
 * Parse a decimal string into minor units.
 *
 * String in, integer out: taking a JavaScript number here would mean the value
 * had already lost precision before this function saw it.
 */
export function parseAmount(text: string, currency: Currency): Money {
  const exponent = minorUnitExponent(currency);
  if (typeof text !== "string" || !/^\d+(\.\d+)?$/.test(text)) {
    throw new ValidationError(`amount ${String(text)} is not a non-negative decimal string`);
  }

  const [whole = "0", fraction = ""] = text.split(".");
  if (fraction.length > exponent) {
    throw new ValidationError(
      `${currency} has ${exponent} minor digits, but ${text} has ${fraction.length}`,
    );
  }

  const padded = fraction.padEnd(exponent, "0");
  return money(Number(whole) * 10 ** exponent + Number(padded || "0"), currency);
}
