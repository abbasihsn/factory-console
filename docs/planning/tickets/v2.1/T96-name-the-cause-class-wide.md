# [T96] The wrong-cause error and the degrading guard, everywhere else

milestone: v2.1 · track: backend · depends_on: T88 · provides: T88's Amendment 1 applied as a CLASS — `invalid_ticket_id` is never returned for a well-formed id anywhere in the codebase, `O_NOFOLLOW`'s absence degrades to a refusal rather than to no check, and the containment gate has one implementation instead of two.

## Context

**Split out of T88 after seven review rounds, with nothing high-severity open.** T88's Amendment 1
fixed one instance of "the error names the wrong cause". Round 3 of the resumption then found the
same defect **in two files T88 was not allowed to touch**, plus two related items. This is the T80
pattern exactly: fix the instance, and the class is still out there.

## The three items

### 1. `invalid_ticket_id` for a well-formed id — the other two sites

`ticket_md.py` and `write_render.py` still raise `400 invalid_ticket_id` for a **well-formed** id
whose path escapes the root. That is the precise defect Amendment 1 fixed in `runs.py`, and the same
argument applies without modification:

> An error must name the condition that actually occurred. A `400 invalid_ticket_id` for a valid id
> is an **accusation about the wrong thing** — the operator checks the id, finds it correct, and has
> been sent away from the real cause.

They were out of T88's Critical files, which is why they survived. **Out of scope is not the same as
not a defect**, and the audit trail has to say which one it was.

### 2. `O_NOFOLLOW`'s absence degrades to NO CHECK

```python
getattr(os, "O_NOFOLLOW", 0)
```

On a platform lacking the flag this silently becomes `0` — **the symlink check disappears**, and the
open proceeds. Compare `resolve_or_none` in the same module, which degrades to a **refusal**. Two
guards against the same class of attack, degrading in opposite directions.

This is the fail-open the whole run-state family was about, in a security guard rather than a write
gate: **a check that cannot be performed must refuse, never pass.** Linux has `O_NOFOLLOW`, so this is
not reachable on the deployment target today — which is exactly why it needs a Ticket rather than a
memory.

### 3. The containment gate has two implementations

`_safe_artifact_path` and `read_last_stop` reimplement it rather than share it. Two copies of a
security check drift, and the T80 amendments are six instances of what that costs.

## Acceptance criteria

1. No code path returns `invalid_ticket_id` for an id that passes the id validator. Asserted as the
   **converse**: a well-formed id whose path escapes the root returns the containment refusal, naming
   the path condition.
2. `O_NOFOLLOW` unavailable → the open **refuses**. A test simulates its absence and asserts the
   refusal, not the fallthrough.
3. One containment implementation, consumed by every caller. A test that would pass against either
   copy is not sufficient — assert there is one.
4. The inline `.resolve()` that can raise `RuntimeError` on CPython <= 3.12 is hardened the same way
   `resolve_or_none` is.
5. Every existing test still passes unchanged.
