/**
 * Money as integer minor units.
 *
 * The float tests are the point of the module. A ledger that stores
 * 0.30000000000000004 has lost the property it exists to provide, and it loses
 * it silently — nothing fails until someone reconciles.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ValidationError } from "../src/errors.js";
import {
  add,
  compare,
  format,
  isSupportedCurrency,
  isZero,
  minorUnitExponent,
  money,
  parseAmount,
  subtract,
  sum,
  supportedCurrencies,
  zero,
} from "../src/money.js";

describe("money", () => {
  it("accepts a non-negative safe integer", () => {
    assert.deepEqual(money(1250, "USD"), { amount: 1250, currency: "USD" });
  });

  it("rejects a fractional amount", () => {
    assert.throws(() => money(12.5, "USD"), ValidationError);
  });

  it("rejects a negative amount", () => {
    assert.throws(() => money(-1, "USD"), ValidationError);
  });

  it("rejects an amount beyond safe integer range", () => {
    // Past 2^53 addition silently stops being exact.
    assert.throws(() => money(Number.MAX_SAFE_INTEGER + 2, "USD"), ValidationError);
  });

  it("rejects NaN and Infinity", () => {
    assert.throws(() => money(Number.NaN, "USD"), ValidationError);
    assert.throws(() => money(Number.POSITIVE_INFINITY, "USD"), ValidationError);
  });

  it("rejects an unsupported currency", () => {
    assert.throws(() => money(100, "XYZ"), ValidationError);
  });
});

describe("arithmetic", () => {
  it("adds without floating point error", () => {
    // 0.10 + 0.20 in minor units is exactly 30, never 30.000000000000004.
    const total = add(money(10, "USD"), money(20, "USD"));
    assert.equal(total.amount, 30);
    assert.equal(Number.isInteger(total.amount), true);
  });

  it("stays exact across many additions", () => {
    let total = zero("USD");
    for (let index = 0; index < 1000; index += 1) {
      total = add(total, money(1, "USD"));
    }
    assert.equal(total.amount, 1000);
  });

  it("refuses to mix currencies when adding", () => {
    assert.throws(() => add(money(100, "USD"), money(100, "EUR")), ValidationError);
  });

  it("subtracts", () => {
    assert.equal(subtract(money(100, "USD"), money(40, "USD")).amount, 60);
  });

  it("refuses a subtraction that would go negative", () => {
    assert.throws(() => subtract(money(40, "USD"), money(100, "USD")), ValidationError);
  });

  it("compares", () => {
    assert.equal(compare(money(1, "USD"), money(2, "USD")), -1);
    assert.equal(compare(money(2, "USD"), money(1, "USD")), 1);
    assert.equal(compare(money(2, "USD"), money(2, "USD")), 0);
  });

  it("refuses to compare across currencies", () => {
    assert.throws(() => compare(money(1, "USD"), money(1, "EUR")), ValidationError);
  });

  it("sums a list", () => {
    assert.equal(sum([money(10, "USD"), money(20, "USD"), money(5, "USD")], "USD").amount, 35);
  });

  it("sums an empty list to zero in the named currency", () => {
    const total = sum([], "JPY");
    assert.equal(total.amount, 0);
    assert.equal(total.currency, "JPY");
  });

  it("knows zero", () => {
    assert.equal(isZero(zero("USD")), true);
    assert.equal(isZero(money(1, "USD")), false);
  });
});

describe("currencies", () => {
  it("knows minor unit exponents", () => {
    assert.equal(minorUnitExponent("USD"), 2);
    assert.equal(minorUnitExponent("JPY"), 0);
  });

  it("reports support", () => {
    assert.equal(isSupportedCurrency("USD"), true);
    assert.equal(isSupportedCurrency("XYZ"), false);
    assert.ok(supportedCurrencies().includes("TWD"));
  });

  it("throws for an unknown exponent", () => {
    assert.throws(() => minorUnitExponent("XYZ"), ValidationError);
  });
});

describe("formatting", () => {
  it("formats a two-digit currency", () => {
    assert.equal(format(money(1250, "USD")), "12.50 USD");
  });

  it("pads minor units", () => {
    assert.equal(format(money(1205, "USD")), "12.05 USD");
    assert.equal(format(money(5, "USD")), "0.05 USD");
  });

  it("formats a zero-digit currency without a point", () => {
    assert.equal(format(money(1250, "JPY")), "1250 JPY");
  });
});

describe("parsing", () => {
  it("parses a decimal string into minor units", () => {
    assert.equal(parseAmount("12.50", "USD").amount, 1250);
  });

  it("parses a whole number", () => {
    assert.equal(parseAmount("12", "USD").amount, 1200);
  });

  it("pads a short fraction", () => {
    assert.equal(parseAmount("12.5", "USD").amount, 1250);
  });

  it("parses a zero-digit currency", () => {
    assert.equal(parseAmount("1250", "JPY").amount, 1250);
  });

  it("rejects more precision than the currency has", () => {
    assert.throws(() => parseAmount("12.505", "USD"), ValidationError);
    assert.throws(() => parseAmount("12.5", "JPY"), ValidationError);
  });

  it("rejects a negative or malformed string", () => {
    for (const text of ["-1.00", "abc", "", "1.2.3", "1,50", " 1.00"]) {
      assert.throws(() => parseAmount(text, "USD"), ValidationError, text);
    }
  });

  it("round trips through format for representative values", () => {
    for (const text of ["0.00", "0.01", "12.50", "9999.99"]) {
      assert.equal(format(parseAmount(text, "USD")), `${text} USD`);
    }
  });
});
