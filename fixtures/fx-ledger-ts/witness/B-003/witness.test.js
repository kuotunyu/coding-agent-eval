/**
 * Witness for fx-ledger-ts/B-003.
 *
 * Overlaid at run time, never part of `tree/`.
 *
 * `Api.handle` converts its own error types into documented statuses and lets
 * anything else escape. What escapes is dealt with by the server, which answers
 * a bare internal_error carrying nothing of its own. Nothing in the fixture's
 * suite drives an unexpected failure through `handle`, so nothing checks that
 * its text stays inside the process.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { App } from "../../dist/src/app.js";

const AT = 1_700_000_000_000;
const INTERNAL = "ENOENT: no such file or directory, open /srv/ledger/private/signing-key";

function appWithFailingTrialBalance() {
  const app = new App({ adminToken: "admin-token", clock: () => AT });
  app.ledger.trialBalance = () => {
    throw new Error(INTERNAL);
  };
  return app;
}

const adminRequest = {
  method: "GET",
  path: "/admin/trial-balance",
  body: "",
  headers: { authorization: "Bearer admin-token" },
};

describe("unexpected failures", () => {
  it("does not answer with the internal error text", async () => {
    const app = appWithFailingTrialBalance();
    let response;
    try {
      response = await app.api.handle(adminRequest);
    } catch {
      // Escaping is the documented behaviour: the server turns it into a bare
      // internal_error, so nothing from inside reaches the caller.
      return;
    }
    assert.ok(
      !JSON.stringify(response.body).includes("signing-key"),
      "an internal filesystem path reached the client",
    );
  });

  it("still converts its own errors into their statuses", async () => {
    const app = new App({ adminToken: "admin-token", clock: () => AT });
    const response = await app.api.handle({
      method: "GET",
      path: "/accounts/asset:nobody",
      body: "",
      headers: {},
    });
    assert.equal(response.status, 404);
  });
});
