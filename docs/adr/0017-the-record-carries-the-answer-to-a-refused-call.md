# ADR-0017: The record carries the answer to a refused call

Status: accepted (2026-10). Amends ADR-0013 (idempotency and crash-window recovery).

## Context
ADR-0013 makes every mutating tool idempotent on `request_id`: the response is journaled after the commit, and if a crash falls between commit and journal, the events still carry the `request_id` and the response is reconstructed from them. The reconstruction knew how to rebuild a successful `reserve_evaluation` and, for everything else, returned a generic `ok: true` with the committed events. That is wrong in two ways found by the kernel validation of 0.4.0. A `reserve_evaluation` can be refused (`budget_exhausted`, `invalid_query`, a driver that stops) and still commit events, namely the operator's decision cost, the rejected point or the `stopped` event; a retry after a crash in the window was told it had succeeded, without an `evaluation_id`. And a retried `settle_evaluation`, `cancel_evaluation`, `run_campaign` or `create_campaign` got the generic shape instead of the original one.

## Decision
When a call is refused after committing events, the refusal (`code`, `message`, `retryable`, `next_actions`, `details`) is written into the payload of the last event the call committed, under the key `refusal`, in the same fsynced append. The journal entry is still written after the commit, as before (committed refusals were already journaled); the record now holds the same answer, so the journal can be lost without losing it. `Toolbox._reconstructed` returns, in this order:

1. the recorded refusal, as `ok: false`, flagged `replayed` and `reconstructed`;
2. for a record that predates this decision (no `refusal` key), the refusal inferred from the committed events (`rejected` gives `invalid_query`; `stopped` with another reason gives `campaign_stopped`; otherwise `budget_exhausted`), with the original details unavailable and a pointer to the events it was read from;
3. for a success, the result keys the original tool returned (create, reserve, settle, cancel, run, stop); the budget and status blocks describe the campaign as it is now.

It never returns `ok: true` for a `reserve_evaluation` that left no `evaluation_id`.

Rejected alternatives: writing the journal entry before the commit (a crash then leaves an answer for a call that never happened, and the record would have to be consulted to tell); a new event kind for refusals (a schema change for what is an attribute of an existing event; ADR-0005).

## Consequences
`payload.refusal` is an optional key on `driver_proposal`, `rejected` and `stopped` events; readers that ignore unknown payload keys, including every reader in this package, are unaffected, and the event schema stays `cadaques.events/2`. A refused call that commits nothing is not journaled and not reconstructed; retrying it simply refuses again. The status and budget blocks of a reconstructed response are current, not historical, which the response does not claim to be.
