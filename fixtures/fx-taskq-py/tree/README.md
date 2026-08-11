# taskq

A small background task queue with an HTTP API, a worker loop, and SQLite
persistence. No third-party dependencies.

## Behavioural guarantees

These are the properties the implementation is expected to hold. They are stated
precisely because they are what the tests check.

### Delivery

- **At-least-once delivery.** A leased task that is neither acknowledged nor
  explicitly failed before its lease expires becomes available again.
- **A task is leased to at most one worker at a time.** Leasing is serialised
  through a single transaction.
- **Leases expire.** A task leased at time `t` with lease duration `d` is
  available again from `t + d`, whether or not the worker still exists.

### Retries

- **`max_attempts` is a limit on attempts, not on retries.** A task configured
  with `max_attempts = 3` is executed at most three times in total.
- Once attempts are exhausted the task moves to `dead` and is never leased again.
- Retry delay grows exponentially: `base_delay * 2 ** (attempts - 1)`, capped at
  `max_delay`.

### Ordering

- Tasks are leased in priority order, then oldest-first within a priority.
  Priority is an integer; higher runs first.

### Authentication

- Admin routes require a bearer token.
- Token comparison is constant time.
- A request without a token and a request with a wrong token are answered
  identically, so neither reveals whether a token exists.

### Input handling

- Payloads are JSON objects and are stored verbatim.
- A payload larger than `max_payload_bytes` is rejected with 413.
- Queue names match `^[a-z0-9][a-z0-9_-]{0,63}$`. Anything else is rejected with
  400 before it reaches storage.

### Persistence

- Enqueue is durable: a task acknowledged as enqueued survives a restart.
- The schema is created on first use and migrated forward by version number.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/queues/{queue}/tasks` | no | Enqueue a task |
| `POST` | `/queues/{queue}/lease` | no | Lease the next runnable task |
| `POST` | `/tasks/{id}/ack` | no | Mark the matching lease generation complete |
| `POST` | `/tasks/{id}/fail` | no | Fail the matching lease generation; retry or dead-letter |
| `GET` | `/tasks/{id}` | no | Task status |
| `GET` | `/admin/stats` | yes | Counts by state |
| `POST` | `/admin/purge` | yes | Delete terminal tasks |

Lease responses include a monotonically increasing `lease_generation`. Ack and
fail requests must return it in their JSON body; an expired or superseded
generation receives 409 and cannot mutate the current lease.

## Running

```
python -m taskq.server --database taskq.db --port 8080
```

## Licence

MIT. See [LICENSE](LICENSE).
