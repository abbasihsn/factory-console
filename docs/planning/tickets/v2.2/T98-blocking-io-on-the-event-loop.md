# [T98] Every v1 handler is `async` and touches the filesystem

milestone: v2.2 · track: backend · depends_on: T86 · source: T86 deep review round 2 (2026-08-05), finding `e22915783d59`, medium/55

## Context

`list_runs` is `async def`, so `RunService.list_run_records`' two synchronous `os.open` + read calls
**per manifest ticket** run inline on the asyncio event loop, with no pagination. While they run,
every other route stalls — including the SSE stream the live view depends on.

The review verified this is **not** a defect `/runs` introduced: it is a house-wide pattern, every
v1 handler is `async` and touches the filesystem. What `/runs` changes is the magnitude — it does
roughly twice the per-ticket I/O of its peers, over a list whose length is the whole manifest, so
the pattern's cost stops being theoretical at the exact endpoint v2.1 added.

**This is the same fault line as T86's `ledger.py` fix**, which is why it was found beside it. That
one was acute — a FIFO blocking the loop *forever*. This one is chronic: bounded work on the wrong
thread, invisible until the manifest is large or the disk is slow. Fixing the acute case and leaving
the chronic one is how the next `/spend`-shaped hang gets built.

**Non-blocking.** Round 2 returned `any_high_open: 0`; v2.1's Version is not gated on this. Filed so
a house-wide pattern gets a house-wide decision rather than being re-found per endpoint.

## Acceptance criteria

1. A stated, written decision on the pattern — either every filesystem-touching handler goes through
   a threadpool offload (`anyio.to_thread.run_sync` / `run_in_executor`), or the handlers that do
   blocking I/O stop being `async def`. **One rule, recorded in `ARCHITECTURE.md`**, not a
   per-endpoint judgement, since the per-endpoint judgement is what produced the current state.
2. `/runs` conforms to it, as the endpoint with the largest per-request I/O.
3. A test that proves the event loop stays responsive during a slow artifact read — a concurrent
   request served while one is blocked in I/O. A test that merely asserts the offload was
   *configured* passes on the bug.
4. Decide pagination for `/runs` explicitly: either bound the response, or record why an unbounded
   manifest-length list is acceptable and what caps it in practice. Silence here reads as "nobody
   considered it".
5. Check the SSE stream specifically. It is the route with the longest-lived connection and the one
   whose failure mode — a live view that silently stops updating — is the hardest to notice, which
   is the same class of defect T91 was written for.
