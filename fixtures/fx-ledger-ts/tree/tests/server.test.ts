/**
 * The HTTP server itself.
 *
 * Everywhere else drives `Api` directly, which is the right way to test routing
 * and policy but says nothing about the process wrapped around it. The entry
 * point used to call `process.exit` on `main`'s return value, so the process
 * stopped the instant it began listening and the service never answered
 * anything. Nothing caught that, because nothing here started a server.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import type { Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { App } from "../src/app.js";
import { FileJournal } from "../src/journal.js";
import { main, serve } from "../src/server.js";

const TOKEN = "admin-token";

function addressOf(server: Server): string {
  const address = server.address();
  assert.ok(address !== null && typeof address === "object");
  return `http://127.0.0.1:${address.port}`;
}

/** Wait until the server is actually accepting connections. */
function listening(server: Server): Promise<void> {
  return new Promise((resolve) => {
    if (server.listening) {
      resolve();
      return;
    }
    server.once("listening", () => resolve());
  });
}

describe("the served application", () => {
  let directory: string;
  let server: Server;
  let base: string;

  before(async () => {
    directory = mkdtempSync(join(tmpdir(), "ledger-server-"));
    const app = new App({
      journal: new FileJournal(join(directory, "journal.jsonl")),
      adminToken: TOKEN,
    });
    // Port 0: the OS picks a free one, so tests never collide.
    server = serve(app, "127.0.0.1", 0);
    await listening(server);
    base = addressOf(server);
  });

  after(() => {
    server.close();
    rmSync(directory, { recursive: true, force: true });
  });

  it("answers health over a real socket", async () => {
    const response = await fetch(`${base}/health`);
    assert.equal(response.status, 200);
  });

  it("opens an account and reads it back", async () => {
    const created = await fetch(`${base}/accounts`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ id: "asset:alice", type: "asset", currency: "USD" }),
    });
    assert.equal(created.status, 201);

    const fetched = await fetch(`${base}/accounts/asset:alice`);
    assert.equal(fetched.status, 200);
  });

  it("still refuses an admin route without a token", async () => {
    const response = await fetch(`${base}/admin/trial-balance`);
    assert.equal(response.status, 401);
  });

  it("serves several requests on one running process", async () => {
    // The failure this guards was not a wrong answer, it was no answer at all
    // after the first moment.
    for (let index = 0; index < 3; index += 1) {
      assert.equal((await fetch(`${base}/health`)).status, 200);
    }
  });
});

describe("the entry point", () => {
  let server: Server;
  let directory: string;

  before(async () => {
    directory = mkdtempSync(join(tmpdir(), "ledger-main-"));
    server = main(["--journal", join(directory, "j.jsonl"), "--port", "0"]);
    await listening(server);
  });

  after(() => {
    server.close();
    rmSync(directory, { recursive: true, force: true });
  });

  it("returns a listening server rather than an exit code", async () => {
    // An exit code would have to be produced before the server had done
    // anything, and treating it as the outcome is what stopped the process.
    assert.equal(server.listening, true);
    assert.equal((await fetch(`${addressOf(server)}/health`)).status, 200);
  });
});

describe("running the built server as a process", () => {
  /**
   * Calling `main` from a test does not execute the module's entry guard, and
   * the guard is where the defect lived: it called `process.exit` on `main`'s
   * return value, so the process stopped the moment it started listening. The
   * only test that can see that is one which actually runs the built file.
   */
  it("keeps serving instead of exiting immediately", async () => {
    const built = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "server.js");
    const directory = mkdtempSync(join(tmpdir(), "ledger-spawn-"));
    const port = 8100 + Math.floor(Math.random() * 800);
    const child = spawn(
      process.execPath,
      [built, "--journal", join(directory, "j.jsonl"), "--port", String(port)],
      { stdio: "ignore" },
    );

    try {
      let status = 0;
      for (let attempt = 0; attempt < 50 && status !== 200; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        try {
          status = (await fetch(`http://127.0.0.1:${port}/health`)).status;
        } catch {
          // Not up yet, or already dead. Both look the same from here, which is
          // why this retries rather than failing on the first refusal.
        }
      }
      assert.equal(status, 200, "the built server never answered");
      assert.equal(child.exitCode, null, "the process exited instead of serving");
    } finally {
      child.kill();
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
