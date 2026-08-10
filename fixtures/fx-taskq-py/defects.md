# fx-taskq-py — clean tree defect audit

- **Fixture version**: 1.0.3
- **Audited**: 2026-08-05 (re-audited at 1.0.1, 1.0.2, and 1.0.3)
- **Auditor**: kuotunyu (also the fixture author — see the data card)
- **Result at 1.0.3**: four benchmark-scope defects found and fixed — two at
  1.0.0, one at 1.0.1, and one at 1.0.2 that **this audit missed entirely** and
  an agent found the next day. `known_residual_defects.yaml` is empty again,
  which is what v0.1 requires (design spec §6.8).
- **Not found by a person.** The 1.0.2 defect was found by `gpt-5.6-luna` with
  reasoning disabled, on a clean-control run costing $0.0164. Two author passes
  over the same file had missed it.
- **Seeded bugs**: four, described under "Seeded bugs" below. None of them is
  in `tree/`; each exists only as a patch, and this file audits the tree
  before any patch is applied.

> **1.0.0 was not clean.** The audit below originally recorded `server.py` as
> "not settled by reading" and left it there. It was settled by running it, and
> the answer was that the service did not work at all when started the way its
> README documents. See "Defects found at 1.0.0" before trusting anything else
> in this file.

This audit is the evidence behind `benchmark_unsupported_findings_per_kloc`.
That metric counts every finding an agent reports on the clean control as
unsupported, so it is only honest if the tree really is clean. This file is
where that claim is made checkable rather than asserted.

## Scope

- **In scope**: `src/**` — 1,367 counted lines across 16 files (15 modules plus
  a package `__init__.py` holding only the version string).
- **Out of scope**: `tests/**`. A defect in a test is not a defect in the
  service, and counting one would make an agent's correct silence look like a
  miss.
- **Categories audited**: `correctness`, `security`, `concurrency`,
  `data_boundary`, `release_claim`.

## Defect found at 1.0.1

### A queue name ending in a newline was accepted (`correctness`)

`validate_queue_name` used `re.match` against a pattern anchored with `$`. In
Python `$` also matches immediately before a final newline, so `"emails
"`
passed validation while `"emails
reports"` did not — which is why reading the
pattern suggested it was anchored at both ends. This audit had said exactly
that, and it was wrong.

The consequence is quiet. `"emails
"` becomes a queue distinct from
`"emails"`, so an enqueue succeeds, returns a task, and no worker polling
`"emails"` ever reads it. The work is accepted and then orphaned. It is not
reachable over HTTP, because a request line cannot carry a raw newline, but it
is reachable by any programmatic caller including the shipped client.

Fixed with `fullmatch`. Mutating it back fails
`test_enqueue_rejects_a_queue_name_with_a_trailing_newline`.

## Defects found at 1.0.0

Both lived in `server.py`, which had no test file at all. Every other module is
driven directly by the suite; the server was reached only by `python -m
taskq.server`, which nothing ran. `tests/test_server.py` now exists and drives a
real socket, which is the only way either of these is visible.

### The service did not work when run (`correctness`, `release_claim`)

`Storage` opened its SQLite connection with the default `check_same_thread=True`
and `serve()` used `ThreadingHTTPServer`, so every request was handled on a
worker thread that had not opened the connection. SQLite's Python binding
refuses that:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used
in that same thread.
```

`/health` is the only route that does not touch storage, so a smoke check
passed while enqueue, lease, ack, fail, status and every admin route returned
500. The README documents `python -m taskq.server` as the way to run this, so
the fixture claimed to be a working service and was not.

Fixed by opening the connection with `check_same_thread=False` and holding a
lock across `write_transaction`. SQLite is built serialized, so sharing one
handle between threads is safe; what is not safe is two threads interleaving
`BEGIN IMMEDIATE ... COMMIT` on it, and the lock is what prevents that.

`ThreadingHTTPServer` was briefly replaced with a single-threaded `HTTPServer`
during the fix, and then put back: once `Storage` is thread-safe, a mutation
test restoring the threading server fails nothing, which is the evidence that
the server class was never the defect. The connection was.

### A 204 announced a body it did not send (`correctness`)

`_dispatch` set `Content-Length` from the encoded body for every status, then
skipped the write for 204. A lease on an empty queue therefore promised two
bytes and sent none. On a keep-alive connection — the handler sets
`protocol_version = "HTTP/1.1"` — the client waits for bytes that never arrive
and then reads the next response as the tail of this one.

Fixed by answering 204 with `Content-Length: 0`, no body, and no `Content-Type`.

### Also changed

`_Handler.timeout` is now set. The body read was already capped, which bounds
how much memory a request can take, but nothing bounded how long it would wait
for bytes a client promised and never sent.

## Method

Each module was read against the guarantees in `tree/README.md`, and each
guarantee was traced to the code that implements it. Where a property could not
be settled by reading — timing behaviour, transaction interleaving — the note
says so rather than claiming more confidence than the method supports.

## Module-by-module

### `errors.py`

Statuses are attached to exception classes, so the API layer cannot map an error
to a status inconsistently. `Unauthorized` carries one message for every
authentication failure, which is what stops the response distinguishing a
missing token from a wrong one.

### `config.py`

No secrets are defaulted. `admin_token` defaults to `None`, and `auth.py` treats
that as closed rather than open. Numeric settings are parsed with `int`/`float`
and will raise on nonsense rather than silently coercing.

### `models.py`

`ALLOWED_TRANSITIONS` is a closed table and terminal states have no outgoing
edges, so a done or dead task cannot be moved. `attempts_remaining` clamps at
zero, so a task whose attempts were somehow over-counted reports 0 rather than a
negative number that a caller might treat as "unlimited".

### `util.py`

`validate_queue_name` uses `fullmatch`, so a name containing a newline cannot
pass by matching only its first line — and neither can one that merely ends in
a newline, which `match` would have allowed even with the pattern anchored. See
the 1.0.1 finding above. `retry_delay` raises below
one attempt rather than computing `base * 2 ** -1`, which would return half the
base delay and look plausible.

### `storage.py`

Every statement is parameterised; there is no string interpolation into SQL
anywhere in the module, and a test inserts a queue name containing a quote and
a `DROP TABLE` to confirm it is stored rather than executed.

`write_transaction` uses `BEGIN IMMEDIATE`. A deferred transaction would let two
workers each read the same candidate row before either wrote, which is the
classic double-lease. Rollback happens on `BaseException` rather than
`Exception`, so a `KeyboardInterrupt` mid-transaction does not leave one open.

**Not settled by reading**: whether the transaction is genuinely serialised
under real concurrent load. That needs a concurrency harness, and the property
is asserted only for the single-connection case the tests cover.

### `queue.py`

`max_attempts` is compared with `>=` against `attempts`, and `lease` increments
`attempts` before the handler runs, so a task with three attempts executes three
times. The `attempts_remaining` test and the loop in
`test_max_attempts_counts_attempts_not_retries` both pin this.

The capacity check sits inside the leasing transaction. Outside it, two workers
could each observe `limit - 1` active leases and both proceed.

Validation runs before the idempotency lookup, so a malformed request is
rejected rather than answered with whatever a previous good request produced.

### `auth.py`

Comparison is `hmac.compare_digest` on encoded bytes. `==` would stop at the
first differing byte and leak the token a byte at a time; encoding matters
because `compare_digest` raises on non-ASCII `str`.

An unset `admin_token` closes the admin routes. Treating an unset secret as
"no authentication required" is the failure mode where a half-finished
deployment is wide open.

**Not settled by reading**: that the comparison is constant time in practice.
Measuring that needs statistics and a quiet machine. What the tests pin is the
thing that actually regresses — that the call goes through `compare_digest`.

### `api.py`

Route patterns constrain their segments, so a queue name with a slash or a
traversal sequence fails to match rather than reaching a handler. The task
segment is anchored to 32 hex characters.

Every admin route authenticates. `test_admin_routes.py` iterates the list rather
than testing two of them, because adding a route and forgetting to protect it is
the realistic mistake.

Errors are converted by class, so a new error type gets the right status without
anyone editing a mapping.

### `worker.py`

The handler is wrapped in `except Exception`. An escaping exception would leave
the task leased until the lease expired, turning an immediate failure into a
delayed one. `BaseException` is deliberately not caught, so `KeyboardInterrupt`
still stops the worker.

Sleeping happens only when the queue was empty, so a busy queue is not slowed by
an artificial delay.

### `idempotency.py`

Keys are scoped to `(queue, key)`. A key scoped globally would silently drop the
second of two genuinely different pieces of work sharing a natural id.

Expired keys are treated as absent inside the query rather than returned with a
flag, so a caller who forgot to check the expiry cannot reuse a stale task id.

### `limits.py`

`active_leases` counts only unexpired leases. Counting expired ones would let a
single crashed worker hold capacity indefinitely.

### `deadletter.py`

`requeue` resets the attempt counter. Without that, a requeued task would be
leased once and die again immediately, which reads as the requeue having
silently failed.

Paging bounds are validated, so a caller cannot request the whole table with one
large limit.

### `metrics.py`

Ages are computed against an injected clock, so a snapshot is reproducible.
An unused queue reports zeros rather than raising: asking about a queue that has
not been used is normal, and an error would push callers into treating absence
as failure.

### `server.py`

The request body is read with a bound rather than trusting `Content-Length`: an
oversized declared length would otherwise let one request hold the connection
while the server waited for bytes that never arrive. A negative or unparseable
length reads nothing instead of raising.

Logging goes to stderr. Writing to stdout would corrupt output a caller might be
parsing.

`204` responses send no body, which is what the status means.

This module is now covered by `tests/test_server.py`, which drives a real
socket. Both defects recorded above lived here and survived precisely because
"not settled by reading" was allowed to stand as a conclusion rather than as a
task. What is still not settled is behaviour under genuine load — many
simultaneous connections, sustained — which needs a harness rather than a test.
What is settled is that the documented way of running the service works, that
one connection serves several requests in sequence, and that an oversized
payload is refused with 413.

### `client.py`

An idempotency key is generated when the caller does not supply one. A client
that only sends a key when someone remembers to is a client that duplicates work
exactly when a retry happens, which is the case the feature exists for.

## Deliberate non-defects

Things a reviewer might flag which are decisions, not defects:

- **`urllib` rather than a real HTTP client.** The fixture takes no third-party
  dependency, so the prepared image needs no package resolution and its contents
  do not depend on when it was built.
- **`http.server` in `server.py`.** Same reason. It is not a production server
  and the README does not claim otherwise.
- **No authentication on the task routes.** The README's table states which
  routes require a token. This is a documented boundary, not an oversight.
- **SQLite with a single connection.** Documented, and appropriate at this size.
- **`assert` after a write inside a transaction** (`queue.lease`,
  `deadletter.requeue`). These guard an invariant the transaction just
  established, not user input, so running under `-O` losing them is acceptable.

## Seeded bugs

The defects recorded above were in the clean tree and were removed. The ones
below were **put there on purpose**, and exist only as patches under
`patches/`. The tree in `tree/` contains none of them; applying a patch is what
produces a mutated tree, and gate G2 proves each one applies, changes the
declared behaviour, and reverts to the original bytes.

They are listed here because an auditor reading this file needs to know which
parts of the tree get mutated. What each one claims, and why, lives in its
manifest under `bugs/` — the single source for that, so the two cannot drift
apart. Nothing is repeated here.

| Bug | Category | Where it lands |
|---|---|---|
| `B-001` | `security` | `src/taskq/auth.py` — token comparison |
| `B-002` | `data_boundary` | `src/taskq/metrics.py` — the per-queue query |
| `B-003` | `correctness` | `src/taskq/deadletter.py` — listing and paging |
| `B-004` | `release_claim` | `src/taskq/api.py` — the request size check |

### How they were chosen

By mutation screening, not by judgement about what looked fragile. Twenty-three
plausible defects were written, applied one at a time, and the fixture's own
suite re-run against each. Thirteen were caught and ten survived. Only the survivors
were considered, and of those the ones used were picked for spread — different
modules, different categories — rather than for how clever they were.

That filter is not an optimisation, it is the requirement. A seeded defect the
fixture's own tests catch measures whether an agent runs the test suite, not
whether it can read code, and every bug it contributed would inflate recall for
a behaviour the benchmark is not trying to measure. Every bug above survives
the full suite (205 tests) on the mutated tree.

### What the screening said about this tree

Most first guesses being caught is itself an audit result: it says the suite
constrains the module surface more tightly than reading it suggests. The
survivors clustered in the places the suite reasons about least — boundaries
checked from one side only, paths exercised with one argument shape, and error
handling for failures no test provokes. Those are the same places the clean-tree
defects above were found, which is the more useful finding: it is not that these
modules are untested, it is that a test asserting the common case leaves the
boundary unconstrained in both directions at once.

## Defect found by the clean-control run, 2026-08-06 (fixed in 1.0.3)

**Found by**: `gpt-5.6-luna` (reasoning disabled), 27 tool calls, $0.0164, in
`runs/live-05`. Not by a person, and not by this audit.

**Location**: `src/taskq/api.py`, `_enqueue`, the `delay_seconds` coercion.

```text
delay_seconds=float(body.get("delay_seconds", 0.0) or 0.0),
```

**Defect**: `float()` raises `ValueError` and `TypeError`, neither of which is a
`TaskqError`. The dispatcher catches only `TaskqError`, so ordinary malformed
client input escapes the API's documented validation path entirely. Reproduced
three ways against 1.0.2:

| Input | Result |
|---|---|
| `delay_seconds="abc"` | uncaught `ValueError` |
| `delay_seconds=[1]` | uncaught `TypeError` |
| `delay_seconds=NaN` | uncaught `sqlite3.IntegrityError`: NOT NULL constraint failed on `tasks.available_at` |

The third is the worst and the agent predicted it: it said non-finite values
would "pass" validation, and they do — `float("nan")` succeeds, then propagates
into a write that violates a database constraint. This is not a wrong status
code, it is a corrupt write path reachable from an ordinary request body.

**Category**: `correctness`, and arguably `release_claim` — the README's "Input
handling" section describes validation rejecting bad input with 400 before it
reaches storage, and here it reaches storage.

**Why the audit missed it.** Both earlier passes read `_enqueue` for what it
does with well-formed input. `float()` on an attacker-supplied value was read as
a coercion rather than as a call that raises, and the surrounding `except
TaskqError` was read as covering the handler rather than as covering only the
exception family the codebase defines. The same shape as the earlier misses:
a boundary examined from one side.

### What the same run said that did not hold

Recorded because the noise is as much a part of the result as the hit, and
because a reader should be able to see what an unsupported finding looks like.
Of six clean-control findings, one was the defect above. The rest:

| Finding | Verdict |
|---|---|
| Expired leases consume concurrency capacity | **Wrong.** `active_leases` filters on `leased_until > at`, and says why in its docstring. |
| `_fail` crashes on a non-object JSON body | **Wrong.** Guarded by `isinstance(body, dict)`. |
| `GET /admin/dead` ignores `limit`/`offset` | **Not a defect.** Accurate about the code; the README never advertises pagination, so nothing is contradicted. |
| Dead-letter `requeue` is not atomic | **Open.** Structurally accurate — it does read-check-write with no `write_transaction()`, unlike `lease()`. Exploitability unproven; needs a concurrency test. |
| Re-lease leaves `available_at` stale | **Open.** Accurate about `mark_leased`; consequence unproven. |

Two wrong, one immaterial, two open, one real. Every one of them was specific,
well written, and cited a line range — which is what makes the noise metric
necessary rather than obvious.

## Completeness

The completeness claim holds at this size and no further. 1,367 lines across 16
files can be read end to end, and were — twice, since the first pass missed both
defects above by treating an untested module as merely unproven rather than as
unexamined. The same claim about a 30,000-line
service would not be credible, which is why the fixture is bounded (spec §5.4).
