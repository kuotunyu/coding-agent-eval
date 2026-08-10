# fx-ledger-ts — clean tree defect audit

- **Fixture version**: 1.0.2
- **Audited**: 2026-08-05 (re-audited at 1.0.1)
- **Tree at 1.0.2**: byte-identical to 1.0.1. The version moved because the
  fixture gained its four bugs, not because anything under `tree/` changed, so
  the audit below still describes the tree it was written against.
- **Auditor**: kuotunyu (also the fixture author — see the data card)
- **Result**: one benchmark-scope defect found and fixed at 1.0.0, and two more
  found and fixed at 1.0.1; none remaining. `known_residual_defects.yaml` is empty, which is what v0.1
  requires (design spec §6.8).
- **Seeded bugs**: four, described under "Seeded bugs" below. None of them is
  in `tree/`; each exists only as a patch, and this file audits the tree
  before any patch is applied.

This audit is the evidence behind `benchmark_unsupported_findings_per_kloc`.
That metric counts every finding an agent reports on the clean control as
unsupported, so it is only honest if the tree really is clean. This file is
where that claim is made checkable rather than asserted.

## Scope

- **In scope**: `src/**` — 1,183 counted lines across 13 modules.
- **Out of scope**: `tests/**` (1,535 lines). A defect in a test is not a defect
  in the service, and counting one would make an agent's correct silence look
  like a miss.
- **Categories audited**: `correctness`, `security`, `concurrency`,
  `data_boundary`, `release_claim`.

## Method

Each module was read against the guarantees in `tree/README.md`, and each
guarantee was traced to the code that implements it. Where a property could not
be settled by reading — timing behaviour, real concurrent load — the note says
so rather than claiming more confidence than the method supports.

Every fix made during the audit was then mutation-checked: the fix was reverted
one at a time and the suite re-run, to confirm a test actually fails without it.
A fix no test can distinguish is a fix nobody will notice regressing.

## Defects found at 1.0.1

Both were in `server.ts`, and both were found the same way `fx-taskq-py`'s were:
by running the service as its README documents instead of reading it. The module
had no test file. Every other part of this fixture is driven through `Api`
directly, which never starts a process.

### The server exited the moment it began listening (`correctness`, `release_claim`)

The entry point read:

```ts
process.exit(main(process.argv.slice(2)));
```

`main` started the listener and returned 0, so `process.exit(0)` ran
immediately. The listening socket is what would have kept the process alive;
exiting on the return value stopped it before it could answer anything. Started
as documented, the service produced no output, no error, and exit code 0.

`main` now returns the server rather than an exit code — an exit code has to be
produced before the server has done anything, so treating it as the outcome was
the mistake — and the entry point no longer exits.

The test that catches this **spawns the built file**. Calling `main` from a test
does not execute the module's entry guard, and the guard was where the defect
lived; a test that only calls `main` passes against the broken version.

### The documented run command named a path the build never produces (`release_claim`)

The README said `node dist/server.js`. `tsconfig.json` sets `rootDir: "."` and
compiles `src/` and `tests/`, so the entry point is emitted at
`dist/src/server.js`. The documented command failed with `MODULE_NOT_FOUND`.

Rather than repeat the layout in prose, `package.json` now has a `start` script
naming the real path and the README calls `npm start`. A path written in two
places drifts; a path written in one does not.

## Defect found at 1.0.0


### Replay did not restore transfers (`correctness`, `release_claim`)

`App.replay` handled `account_opened` and `transaction_posted` but had no case
that rebuilt a transfer. `TransferService.restore` existed and was never called
from anywhere. The comment in its place claimed transfers were "rebuilt from
the transactions above rather than stored twice", which was not true and could
not be: a transaction records that money moved, a transfer records whose
instruction moved it and whether that instruction has cleared. The second is not
derivable from the first.

The consequences were not cosmetic. `server.ts` replays the journal on start, so
after any restart:

- `GET /transfers/{id}` returned 404 for a transfer the ledger had posted;
- `POST /settlement/run` reported nothing pending, so transfers created before
  the restart could never settle;
- the README's "Replaying the journal from empty reproduces the same state" was
  false, which makes it a `release_claim` defect as well as a correctness one.

Fixed by adding a `transfer_created` journal entry carrying the transfer,
appended before the transfer enters the map — the same write-ahead order every
other state change here uses — and by handling both `transfer_created` and
`transfer_settled` in `replay`. A settlement entry whose transfer is missing now
raises rather than being skipped: a journal that records a transfer settling
without recording the transfer cannot be replayed approximately.

Fixing it exposed a second problem that only exists once replay works. The id
counter was module-level, so it restarted with the process, and a restored
service would mint ids colliding with the ones it had just replayed. The
timestamp component does not save it — the clock is injectable, and a restart
inside one millisecond is not impossible in production either. The counter is
now per-service, and `restore` carries it past the highest ordinal it has seen.
A transfer and its transaction share one ordinal, so a single counter covers
both. The batch counter in `settlement.ts` was module-level for the same reason
and was moved to the service for consistency; nothing depended on its being
global.

Both are covered: removing the `transfer_created` case fails 5 tests, and
removing the counter carry-over fails `does not reissue an id it just replayed`.

## Two smaller repairs

Neither of these had reached a wrong answer, but both were reachable and both
would have been reasonable findings against the clean control.

- **`Release` was not idempotent.** Releasing twice decremented `heldCount`
  twice, and `heldCount() === 0` is exactly what the tests use to assert no lock
  was leaked — so a double release could mask a genuine leak. A second call is
  now a no-op. `acquireAll` also reversed its release list in place, so calling
  its release twice would have released in the wrong order; it now reverses a
  copy.
- **Postings were immutable only at compile time.** `readonly` is erased by the
  compiler, and "postings are immutable" is a guarantee this ledger makes to
  callers that do not necessarily compile against its types. They are now frozen
  in `Ledger.apply`, which is the single point every posting passes through —
  including the ones replay parses out of JSON and the ones `reversalPostings`
  builds. Freezing in `posting()` as well was tried and removed: it was
  unreachable by any test, since everything it produces is frozen again a moment
  later.

## Module-by-module

### `errors.ts`

Statuses are attached to exception classes, so the API layer cannot map an error
to a status inconsistently. `UnauthorizedError` carries one message for every
authentication failure, which is what stops the response distinguishing a
missing token from a wrong one.

### `money.ts`

Amounts are integer minor units and are validated with `Number.isSafeInteger`,
not `Number.isInteger`. Beyond 2^53 addition silently stops being exact, and a
balance that stops being exact without saying so is the failure this module
exists to prevent.

`parseAmount` takes a string. Accepting a JavaScript number would mean the value
had already lost precision before this function saw it. Its intermediate
`Number(whole) * 10 ** exponent` is checked by `money`, so an input large enough
to be inexact is rejected rather than rounded: any product at or above 2^53
fails `isSafeInteger`, and below it a whole part of fifteen digits or fewer is
exact.

Amounts are non-negative, with direction carried by the posting side. Allowing
both a negative debit and a positive credit would give two spellings for one
fact, and code would eventually disagree about which it meant.

### `accounts.ts`

`NORMAL_SIDE` is a closed table over the account types, and `increasesOn` is the
only place that decides whether a posting adds or subtracts — so the sign of a
balance is settled in one place rather than at each call site.

`prepare` and `register` are separate so the caller can write the journal entry
between them. That split is what makes the write-ahead order in `App.openAccount`
possible; a single `open` method would have made it impossible to express.

Account ids are anchored at both ends, so an id containing a newline cannot pass
by matching only its first line.

### `locks.ts`

Every `await` is a point where another task can run. JavaScript's single thread
removes torn reads, not interleaving, and treating the two as the same thing is
the mistake this module exists to prevent.

`acquire` becomes the new tail before awaiting the previous one, so a caller
arriving mid-await queues behind the current holder rather than the previous
one. `acquireAll` sorts, which is what stops two transfers between the same pair
of accounts in opposite directions from each holding what the other needs.

**Not settled by reading**: nothing here is tested under real parallelism,
because there is none to test — the tests drive interleavings deterministically
by holding a lock and resuming. That is the accurate model for this runtime, and
would not be for a threaded one.

`tails` is never pruned, so it retains one entry per account ever locked. Bounded
by the number of accounts, which only an authenticated admin route can grow.

### `ledger.ts`

`assertBalanced` runs before the journal is touched. Validating afterwards would
leave a record of a transaction the ledger then refused, which is worse than
either outcome alone. It also rejects a transaction that moves zero: it would
balance trivially and record nothing.

`post` appends to the journal before applying. If the process dies between them,
replay reproduces the transaction; the reverse order would lose it.

`canDebit` documents that callers must hold the account's lock across the check
and the write it guards. `TransferService` and `SettlementService` are the only
callers and both do.

### `journal.ts`

`DraftEntry` is distributive on purpose. A plain `Omit<JournalEntry, "seq">`
over a discriminated union collapses to the keys every member shares — here just
`kind` and `at` — so every payload field would be rejected. This is the kind of
thing that fails loudly at the call site, but the reason is not obvious from the
error, so it is written down.

`FileJournal` writes synchronously. An async append would return before the
bytes reached the file, so the caller could update state believing the record
was durable when it was still in a buffer.

A corrupt line raises rather than being skipped. Skipping it would produce a
state that silently omits whatever the line said.

**Not settled by reading**: `read()` casts each parsed line to `JournalEntry`
without validating its shape. A line that is valid JSON but not a valid entry —
which requires an external writer, since nothing here produces one — would reach
`replay`, where an unrecognised `kind` falls through the switch and is ignored.
The journal is treated as trusted storage. This is a documented boundary, not an
input-validation gap: the file is not an input surface.

### `transfers.ts`

Validation runs before the locks, so a malformed request does not queue behind
live work, and before the idempotency lookup, so a malformed request is rejected
rather than answered with whatever a previous good request produced.

The sufficiency check and the post happen under the same locks. Outside them,
two transfers could each read a balance of 100, each decide 60 is affordable,
and leave the account at -20 with both callers told they succeeded.

`markSettled` refuses a transfer that is not pending, so a batch replayed after
a partial failure cannot settle the same transfer twice.

Idempotency keys are global rather than per-account. A transfer is not owned by
either side, so scoping a key to one of them would let the same key mean two
different things depending on which account was consulted.

### `settlement.ts`

Every candidate is re-checked inside the locks before any is marked. Candidates
are selected before the locks are taken — that window is real, and the re-check
is what closes it. The test for it constructs the interleaving by holding an
account lock rather than asserting a state the selection step has already
filtered out.

`runUntilDrained` is bounded rather than `while (true)`. A bug that stopped
draining the queue should fail, not hang.

### `auth.ts`

Comparison goes through `crypto.timingSafeEqual` on encoded bytes. `===` stops
at the first differing byte, so how long a rejection takes tells the caller how
much of their guess was right. Differing lengths are padded and still compared,
because `timingSafeEqual` throws on a length mismatch and the throw would itself
leak the length.

An unset `adminToken` closes the admin routes. Treating an unset secret as "no
authentication required" is the failure mode where a half-finished deployment is
wide open.

**Not settled by reading**: that the comparison is constant time in practice.
Measuring that needs statistics and a quiet machine. What the tests pin is the
thing that actually regresses — that the call goes through `timingSafeEqual`.
The function is called through the `crypto` namespace rather than as a named
import specifically so that test can observe it; a named import of a Node
builtin is a snapshot the test cannot intercept.

### `api.ts`

Routes are compiled patterns rather than split paths, so a segment containing a
slash or a traversal sequence fails to match instead of being interpreted.

Method mismatch on a matching path returns 405 rather than 404, so a caller
learns the path exists — which is not a secret, since the route table is in the
README.

Every admin route calls `requireAdmin` as its first statement. The test iterates
the route list rather than checking two of them, because adding a route and
forgetting to protect it is the realistic mistake.

Errors are converted by class, so a new error type gets the right status without
anyone editing a mapping.

### `server.ts`

The request body is read with a hard cap rather than trusting `Content-Length`:
an oversized declared length would otherwise let one request hold a connection
open waiting for bytes that never arrive.

Logging goes to stderr. Writing to stdout would corrupt output a caller may be
parsing.

`tests/server.test.ts` now starts a real server and, separately, spawns the
built entry point. Both defects above lived here and survived because this file
did not exist.

**Still not settled**: behaviour under genuine concurrent load. `node:http`
handles requests on one event loop and the locks serialise the state changes,
but nothing here drives many simultaneous connections, so that part still rests
on the argument rather than on a test.

### `app.ts` and `idempotency.ts`

`openAccount` validates, journals, then registers — the write-ahead order.
Registering first would let a crash leave an account in memory the journal never
mentions, and replay would then silently drop it.

Replay never appends to the journal. An append during replay would grow the file
on every start, and the journal would eventually be mostly its own echo. A test
pins this.

Expired idempotency keys read as absent rather than being returned with a flag,
so a caller who forgot to check the expiry cannot resurrect a transfer id from
arbitrarily long ago.

## Deliberate non-defects

Things a reviewer might flag which are decisions, not defects:

- **`node:http` rather than a framework, and no third-party runtime
  dependency.** The prepared image then contains no resolved runtime packages,
  and the service's behaviour does not depend on when it was built. It is not a
  production server and the README does not claim otherwise.
- **In-memory state with a JSONL journal.** Documented, and appropriate at this
  size. Replay is what makes it defensible, which is why the replay gap above
  was treated as a real defect rather than a limitation.
- **No authentication on the transfer routes.** The README's table states which
  routes require a token. This is a documented boundary, not an oversight.
- **`FileJournal.size()` re-reads the whole file.** O(n) per call, called once
  per test assertion and never on a request path.
- **The settlement batch locks every account the batch touches.** Coarse on
  purpose: a batch that locked incrementally could observe a transfer created
  against one of its accounts part way through.
- **Ids are sequential rather than random.** They are not capability tokens —
  every transfer route is unauthenticated by design, so guessing an id grants
  nothing that enumerating them would not. The ordering is used as a tie-break
  between transfers created in the same millisecond.

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
| `B-001` | `concurrency` | `src/settlement.ts` — the batch's guard |
| `B-002` | `correctness` | `src/idempotency.ts` — key expiry |
| `B-003` | `data_boundary` | `src/api.ts` — the error path |
| `B-004` | `security` | `src/auth.ts` — the length-mismatch branch |

### How they were chosen

By mutation screening, not by judgement about what looked fragile. Eighteen
plausible defects were written, applied one at a time, and the fixture's own
suite re-run against each. Twelve were caught and six survived. Only the survivors
were considered, and of those the ones used were picked for spread — different
modules, different categories — rather than for how clever they were.

That filter is not an optimisation, it is the requirement. A seeded defect the
fixture's own tests catch measures whether an agent runs the test suite, not
whether it can read code, and every bug it contributed would inflate recall for
a behaviour the benchmark is not trying to measure. Every bug above survives
the full suite (174 tests) on the mutated tree.

### What the screening said about this tree

Most first guesses being caught is itself an audit result: it says the suite
constrains the module surface more tightly than reading it suggests. The
survivors clustered in the places the suite reasons about least — boundaries
checked from one side only, paths exercised with one argument shape, and error
handling for failures no test provokes. Those are the same places the clean-tree
defects above were found, which is the more useful finding: it is not that these
modules are untested, it is that a test asserting the common case leaves the
boundary unconstrained in both directions at once.

## Completeness

The completeness claim holds at this size and no further. 1,183 lines across 13
modules can be read end to end, and were — which is how the replay defect was
found, since no test covered the path. It is also how the 1.0.1 defects were
missed: reading `server.ts` did not reveal that its entry point never served,
because the mistake was in what the code *did*, not in what it said. Running it
was the only way. The same claim about a 30,000-line
service would not be credible, which is why the fixture is bounded (spec §5.4).
