# [T69] Diff-preview modal + generic confirm dialog + unified-diff renderer

milestone: v2 · track: frontend · depends_on: T29, T31, T66 · provides: DiffPreviewModal.svelte (renders dry-run unified diff, gates save+confirm) + ConfirmDialog.svelte (delete) + lib/diff/unifiedDiff.ts line classifier.

## Context

Every write is gated behind an explicit review: the SPA calls the dry-run endpoint, shows the exact unified diff the write would produce, and only writes after a deliberate save+confirm. Delete gets its own confirm. These are shared, presentational building blocks the detail and create flows compose, kept separate so they stay small and independently testable. Rendering the diff with a tiny local line classifier avoids adding a diff dependency (no extra package.json churn / merge hazard).

## Staged approach

1. Add `src/lib/diff/unifiedDiff.ts`: `parseDiffLines(diff: string): { text: string; kind: 'add'|'del'|'hunk'|'meta'|'context' }[]` classifying each line by its leading marker (`+`/`-`/`@@`/`+++`/`---`).
2. Add `src/lib/components/DiffPreviewModal.svelte`: props `{ open: boolean; preview: WritePreview | null; loading: boolean; error: ApiError | null; onConfirm: () => void; onCancel: () => void }`; render the classified diff lines in a monospaced, color-coded `<pre>`; show a spinner while `loading`, the ApiErrorView when `error`, and gate the 'Save' (confirm) button so it is disabled while loading/error/absent preview.
3. Add `src/lib/components/ConfirmDialog.svelte`: props `{ open, title, message, confirmLabel, danger?, onConfirm, onCancel }` — a generic accessible confirm used for delete.
4. Keep both modals presentational (no `$app/*`, no API calls) with a backdrop + Escape/cancel handling.

## Critical files

- `frontend/src/lib/components/DiffPreviewModal.svelte` (new)
- `frontend/src/lib/components/ConfirmDialog.svelte` (new)
- `frontend/src/lib/diff/unifiedDiff.ts` (new)

## Interface & data

`DiffPreviewModal` props `{ open, preview: WritePreview|null, loading, error: ApiError|null, onConfirm, onCancel }`; `ConfirmDialog` props `{ open, title, message, confirmLabel, danger?, onConfirm, onCancel }`; `parseDiffLines(diff: string)` → classified lines. By reference: `WritePreview` (T66 regenerated dry-run response — read its unified `diff` field; do not redefine); `ApiError` render shape via the existing `ApiErrorView`. No DB. NFR: destructive/mutating actions gated behind explicit confirm; no write is issued from these components (caller owns the write).

## Verification

Vitest specs: `unifiedDiff.test.ts` classifies add/del/hunk/meta/context lines; `DiffPreviewModal.test.ts` renders a sample diff, disables save while loading and shows ApiErrorView on error, fires `onConfirm`/`onCancel`; `ConfirmDialog.test.ts` covers confirm/cancel + Escape. `pnpm check`, `pnpm test`, `pnpm lint` green.
