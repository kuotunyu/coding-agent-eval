/**
 * HTTP routing and request handling.
 *
 * Routes are compiled patterns rather than split paths, so a segment containing
 * a slash or a traversal sequence fails to match instead of being interpreted.
 *
 * Handlers translate between JSON and the services. Policy lives in the
 * services; a rule decided here would be one their own tests never see.
 */

import type { Account, OpenAccountRequest } from "./accounts.js";
import { AccountRegistry } from "./accounts.js";
import { authenticate } from "./auth.js";
import { isLedgerError, ValidationError } from "./errors.js";
import { Ledger } from "./ledger.js";
import { SettlementService } from "./settlement.js";
import { TransferService } from "./transfers.js";

export interface Request {
  readonly method: string;
  readonly path: string;
  readonly body: string;
  readonly headers: Readonly<Record<string, string>>;
}

export interface Response {
  readonly status: number;
  readonly body: unknown;
}

export interface ApiConfig {
  readonly adminToken: string | null;
  readonly maxBodyBytes: number;
}

type Handler = (request: Request, params: Record<string, string>) => Promise<Response> | Response;

interface Route {
  readonly method: string;
  readonly pattern: RegExp;
  readonly handler: Handler;
}

/** Ids are constrained in the pattern, so a bad one never reaches a handler. */
const ACCOUNT_SEGMENT = "(?<id>[a-z0-9][a-z0-9:_-]{2,63})";
const TRANSFER_SEGMENT = "(?<id>tr_[a-z0-9]+_[a-z0-9]+)";

export function header(request: Request, name: string): string | undefined {
  return request.headers[name.toLowerCase()];
}

export class Api {
  private readonly routes: readonly Route[];

  constructor(
    private readonly accounts: AccountRegistry,
    private readonly ledger: Ledger,
    private readonly transfers: TransferService,
    private readonly settlement: SettlementService,
    private readonly config: ApiConfig,
    private readonly clock: () => number,
    private readonly openAccount: (request: OpenAccountRequest, at: number) => Account,
  ) {
    this.routes = [
      { method: "POST", pattern: /^\/accounts$/, handler: (r) => this.handleOpenAccount(r) },
      {
        method: "GET",
        pattern: new RegExp(`^/accounts/${ACCOUNT_SEGMENT}$`),
        handler: (_r, p) => this.getAccount(p),
      },
      { method: "POST", pattern: /^\/transfers$/, handler: (r) => this.createTransfer(r) },
      {
        method: "GET",
        pattern: new RegExp(`^/transfers/${TRANSFER_SEGMENT}$`),
        handler: (_r, p) => this.getTransfer(p),
      },
      { method: "POST", pattern: /^\/settlement\/run$/, handler: (r) => this.runSettlement(r) },
      { method: "GET", pattern: /^\/admin\/trial-balance$/, handler: (r) => this.trialBalance(r) },
      { method: "GET", pattern: /^\/health$/, handler: () => ({ status: 200, body: { status: "ok" } }) },
    ];
  }

  async handle(request: Request): Promise<Response> {
    try {
      if (Buffer.byteLength(request.body, "utf8") > this.config.maxBodyBytes) {
        return { status: 413, body: { error: "payload_too_large", detail: "body too large" } };
      }

      for (const route of this.routes) {
        const match = route.pattern.exec(request.path);
        if (match === null) continue;
        if (route.method !== request.method) {
          return { status: 405, body: { error: "method_not_allowed", detail: route.method } };
        }
        return await route.handler(request, { ...(match.groups ?? {}) });
      }
      return { status: 404, body: { error: "not_found", detail: `no route for ${request.path}` } };
    } catch (error) {
      if (isLedgerError(error)) {
        return { status: error.status, body: { error: error.code, detail: error.message } };
      }
      throw error;
    }
  }

  private parseBody(request: Request): Record<string, unknown> {
    if (request.body.trim() === "") return {};
    let parsed: unknown;
    try {
      parsed = JSON.parse(request.body);
    } catch (cause) {
      throw new ValidationError(`body is not valid JSON: ${String(cause)}`);
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new ValidationError("body must be a JSON object");
    }
    return parsed as Record<string, unknown>;
  }

  private requireAdmin(request: Request): void {
    authenticate(header(request, "authorization"), this.config.adminToken);
  }

  // ------------------------------------------------------------- handlers

  private handleOpenAccount(request: Request): Response {
    this.requireAdmin(request);
    const body = this.parseBody(request);
    const account = this.openAccount(
      {
        id: body["id"] as string,
        type: body["type"] as string,
        currency: body["currency"] as string,
        ...(typeof body["name"] === "string" ? { name: body["name"] } : {}),
        ...(typeof body["allowNegative"] === "boolean"
          ? { allowNegative: body["allowNegative"] }
          : {}),
      },
      this.clock(),
    );
    return { status: 201, body: account };
  }

  private getAccount(params: Record<string, string>): Response {
    const id = params["id"] ?? "";
    const { account, minorUnits } = this.ledger.balance(id);
    return { status: 200, body: { ...account, balanceMinorUnits: minorUnits } };
  }

  private async createTransfer(request: Request): Promise<Response> {
    const body = this.parseBody(request);
    const key = header(request, "idempotency-key") ?? body["idempotencyKey"];

    const transfer = await this.transfers.transfer(
      {
        fromAccountId: body["fromAccountId"] as string,
        toAccountId: body["toAccountId"] as string,
        amount: body["amount"] as number,
        currency: body["currency"] as string,
        ...(typeof body["reference"] === "string" ? { reference: body["reference"] } : {}),
        ...(typeof key === "string" ? { idempotencyKey: key } : {}),
      },
      this.clock(),
    );
    return { status: 201, body: transfer };
  }

  private getTransfer(params: Record<string, string>): Response {
    return { status: 200, body: this.transfers.get(params["id"] ?? "") };
  }

  private async runSettlement(request: Request): Promise<Response> {
    this.requireAdmin(request);
    const body = this.parseBody(request);
    const batchSize = body["batchSize"];
    const result =
      typeof batchSize === "number"
        ? await this.settlement.run(this.clock(), batchSize)
        : await this.settlement.run(this.clock());
    return { status: 200, body: result };
  }

  private trialBalance(request: Request): Response {
    this.requireAdmin(request);
    return { status: 200, body: { currencies: this.ledger.trialBalance() } };
  }
}
