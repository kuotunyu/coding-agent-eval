# ledger

A double-entry ledger with an HTTP API, an append-only journal, and batch
settlement. No third-party runtime dependencies.

## Behavioural guarantees

Stated precisely, because these are what the tests check.

### Money

- **Amounts are integer minor units.** Never floating point. A ledger that adds
  `0.1 + 0.2` and stores `0.30000000000000004` is not a ledger.
- Amounts are non-negative. Direction is expressed by which side of a posting an
  account sits on, not by the sign of the number.
- **Currencies never mix.** Every posting in a transaction shares one currency,
  and an account has exactly one currency for its lifetime.

### Double entry

- **Every transaction balances.** The sum of debits equals the sum of credits,
  and a transaction that does not balance is rejected before anything is
  written.
- A transaction has at least two postings.
- **Postings are immutable.** A mistake is corrected by a reversing transaction,
  never by editing history.

### Balances

- An account's balance is the sum of its postings, in minor units.
- **Asset and expense accounts increase on the debit side**; liability, equity,
  and revenue accounts increase on the credit side.
- Accounts may not go negative unless they are marked `allowNegative`.

### Concurrency

- **A transfer is atomic.** Either every posting lands or none does.
- **Concurrent transfers against one account cannot oversell it.** The balance
  check and the write happen under the same per-account lock, so two transfers
  cannot both observe sufficient funds and both proceed.
- Locks are acquired in a **deterministic order** (account id, ascending), so
  two transfers touching the same pair of accounts cannot deadlock.

### Settlement

- Settlement processes pending transfers in batches.
- **A batch is all-or-nothing.** A failure part way through leaves no partial
  batch applied.
- A settled transfer is never settled twice.

### Idempotency

- A transfer may carry an idempotency key. Replaying the same key returns the
  original transfer rather than creating a second one.
- Keys are scoped per key, globally, since a transfer is not owned by one
  account.

### Persistence

- The journal is append-only. Entries are written before the in-memory state is
  updated, so a crash cannot leave state ahead of the record.
- **Replaying the journal from empty reproduces the same state** — accounts,
  balances, and transfers, including which transfers are still pending and
  which have settled. A transfer created before a restart can be settled after
  it.
- Replay reads the journal; it never appends to it.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/accounts` | yes | Open an account |
| `GET` | `/accounts/{id}` | no | Account detail and balance |
| `POST` | `/transfers` | no | Transfer between two accounts |
| `GET` | `/transfers/{id}` | no | Transfer detail |
| `POST` | `/settlement/run` | yes | Settle pending transfers |
| `GET` | `/admin/trial-balance` | yes | Debits and credits by currency |
| `GET` | `/health` | no | Liveness |

## Running

```
npm run build
npm start -- --journal ledger.jsonl --port 8081
```

## Licence

MIT. See [LICENSE](LICENSE).
