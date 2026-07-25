# [T76] Signed releases + sigstore attestations: sign wheel+sdist and publish cosign/sigstore attestations on tag vX.Y.Z

milestone: v2 · track: infra-devops · depends_on: T26 · provides: release.yml additively signs the built wheel + sdist and publishes sigstore/cosign attestations (keyless, OIDC) on tag vX.Y.Z, with a verification step, alongside the unchanged PyPI trusted publishing.

## Context

v2 completes the supply-chain story: consumers should be able to verify that a published wheel/sdist was built by this repo's tagged release workflow. This extends the existing tag-driven `release.yml` to produce keyless sigstore/cosign signatures + attestations for the dist artifacts and to verify them in-run, without disturbing the OIDC trusted-publishing to PyPI or the GitHub Release creation.

## Staged approach

1. MODIFY `.github/workflows/release.yml` additively.
2. In the `publish` job (which already has `id-token: write` for OIDC and `contents: write` for the Release), add a keyless signing step over `dist/*` using GitHub's artifact attestation (`actions/attest-build-provenance`) and/or `sigstore/gh-action-sigstore-python` for the wheel/sdist — SHA-pin any third-party action with a trailing human-readable ref comment, matching the file's existing pinning convention (see the pypi-publish + gh-release pins).
3. Add a verification step that runs `cosign verify-blob` / `sigstore verify` (or `gh attestation verify`) against the produced signatures/bundles to fail fast if signing didn't produce a verifiable artifact.
4. Attach the signature/bundle files to the GitHub Release by adding them to `softprops/action-gh-release` `files:` glob (they live under `dist/`), or upload as a separate attestation artifact.
5. Keep the `build` job and the tag==version guard unchanged; do not reorder the PyPI publish; add the minimal `attestations: write` permission the attest action requires.
6. Add clear comments explaining the keyless (OIDC) trust model and why it is additive.

## Critical files

- `.github/workflows/release.yml`

## Interface & data

Triggers on the existing `push: tags: ['v*.*.*']`. Consumes the existing `dist/` artifact (wheel + sdist) from the `build` job. Permissions: reuse `id-token: write` (already present for OIDC) and add `attestations: write`; keep `contents: write` for the Release. External actions (SHA-pinned): build-provenance/sigstore signing + a verify step. No DB. NFR: supply-chain integrity (signing/attestation), least-privilege token scopes, no PyPI-publish behavior change.

## Verification

Static: `actionlint .github/workflows/release.yml` and inspect the diff for SHA-pinned actions + minimal permissions. Live validation happens on the next `vX.Y.Z` tag: the run must produce sigstore/cosign bundles for wheel+sdist, pass the in-workflow verify step, and attach them to the GitHub Release, with the PyPI publish unchanged.
