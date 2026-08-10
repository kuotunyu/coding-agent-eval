/**
 * HTTP server entry point.
 *
 * `node:http` from the standard library, so the fixture has no third-party
 * runtime dependency and its prepared image resolves nothing from a registry.
 */

import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { parseArgs } from "node:util";

import { App } from "./app.js";
import type { Request } from "./api.js";
import { FileJournal } from "./journal.js";

const MAX_REQUEST_BYTES = 1024 * 1024;

/**
 * Read a request body with a hard cap.
 *
 * Bounded rather than trusting Content-Length: an oversized declared length
 * would otherwise let one request hold a connection open indefinitely.
 */
async function readBody(request: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;

  for await (const chunk of request) {
    const buffer = chunk as Buffer;
    size += buffer.length;
    if (size > MAX_REQUEST_BYTES) {
      throw new Error("request body exceeds the maximum size");
    }
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function toRequest(incoming: IncomingMessage, body: string): Request {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(incoming.headers)) {
    if (typeof value === "string") {
      headers[key.toLowerCase()] = value;
    }
  }
  return {
    method: incoming.method ?? "GET",
    path: (incoming.url ?? "/").split("?")[0] ?? "/",
    body,
    headers,
  };
}

export function createApp(journalPath: string, adminToken: string | null): App {
  const journal = new FileJournal(journalPath);
  const app = new App({ journal, adminToken });
  app.replay(journal.read());
  return app;
}

export function serve(app: App, host: string, port: number): ReturnType<typeof createServer> {
  const server = createServer((incoming: IncomingMessage, outgoing: ServerResponse) => {
    void (async () => {
      try {
        const body = await readBody(incoming);
        const response = await app.api.handle(toRequest(incoming, body));
        const encoded = JSON.stringify(response.body);

        outgoing.writeHead(response.status, {
          "Content-Type": "application/json; charset=utf-8",
          "Content-Length": String(Buffer.byteLength(encoded, "utf8")),
        });
        outgoing.end(encoded);
      } catch (error) {
        // Logged to stderr, never stdout, which a caller may be parsing.
        process.stderr.write(`request failed: ${String(error)}\n`);
        outgoing.writeHead(500, { "Content-Type": "application/json" });
        outgoing.end(JSON.stringify({ error: "internal_error" }));
      }
    })();
  });

  server.listen(port, host, () => {
    process.stderr.write(`ledger listening on ${host}:${port}\n`);
  });
  return server;
}

/**
 * Start the server and return it.
 *
 * Returns the server rather than an exit code. An exit code would have to be
 * returned before the server had done anything, and a caller that treated it as
 * the outcome would exit while the socket was still listening — which is
 * exactly what the entry point below used to do.
 */
export function main(argv: readonly string[]): ReturnType<typeof createServer> {
  const { values } = parseArgs({
    args: [...argv],
    options: {
      journal: { type: "string", default: "ledger.jsonl" },
      host: { type: "string", default: "127.0.0.1" },
      port: { type: "string", default: "8081" },
    },
  });

  const app = createApp(values.journal ?? "ledger.jsonl", process.env["LEDGER_ADMIN_TOKEN"] ?? null);
  return serve(app, values.host ?? "127.0.0.1", Number(values.port ?? "8081"));
}

// No `process.exit` here. The listening socket is what keeps the process alive;
// exiting on `main`'s return would stop it the instant it began listening.
if (process.argv[1] !== undefined && process.argv[1].endsWith("server.js")) {
  main(process.argv.slice(2));
}
