# Contributor workflow policy

## Branch naming policy

Use exactly one canonical branch format:

```text
<type>/<kebab-case-description>
```

Allowed `<type>` values:
- `feature`
- `fix`
- `docs`
- `chore`
- `refactor`
- `test`
- `release`
- `hotfix`

Enforced rules:
- Lowercase only.
- Hyphen-separated description only (`kebab-case`).
- No underscores, dots, spaces, or uppercase variants.
- Prefix aliases are not allowed (`feat/`, `bugfix/`, `doc/`, and similar near-duplicates are rejected).

Examples:
- ✅ `feature/layer31-benchmark-refresh`
- ✅ `hotfix/validation-schema-regression`
- ❌ `Feature/layer31-benchmark-refresh`
- ❌ `bugfix_layer31`
- ❌ `feat/layer31-benchmark-refresh`

## Source branch → base branch policy

- Pull requests **into `main`** must come only from `release/*` or `hotfix/*`.
- `release/*` and `hotfix/*` branches must target `main`.
- Non-`main` pull requests can use any valid branch type above.

These rules are enforced by required PR checks:
- `Branch naming policy`
- `PR source/base branch policy`

## Tooling reliability policy (`code_review`, push/auth)

### Runtime model pinning

Pin the review model in `/home/runner/work/ASCEND/ASCEND/.github/copilot-runtime.json`:
- Primary model: `gpt-4.1`
- Fallback order: `gpt-4o`, then `o3`

### Required preflight before `code_review`

Run:

```bash
python tools/tooling_preflight.py
```

The preflight:
- validates runtime config structure
- validates configured model availability from `COPILOT_AVAILABLE_MODELS`
- fails fast with a clear error if no supported model is available

### Canonical push/auth path

- Canonical automation push/report path: `engine-tools-report_progress`.
- Do not use direct `git push` in automation runs that rely on agent credentials.
- Local developer pushes can use standard Git auth, but must still satisfy branch protections and required checks.

### Required token scopes and branch-protection expectations

Automation identities must have:
- repository write permission for commit/report paths
- permission to update PR branches where branch protections require it

Branch protections must allow required bot operations without bypassing required checks.

### Transient auth failure runbook

For push/report auth failures:
1. Re-authenticate the identity/session.
2. Refresh or rotate the token with correct scopes.
3. Sync with remote and return to a clean branch state.
4. Re-run preflight and retry once.
5. If failure persists, escalate with failing command output and identity details (no secret values).

### `code_review` HTTP 400 handling

Treat any `code_review` HTTP 400 as an infrastructure blocker:
- stop automated review retries beyond preflight and one retry cycle
- require manual reviewer sign-off
- run `codeql_checker` as the mandatory automated fallback
- add an explicit PR note documenting the `code_review` failure and fallback path used

## Operational rollout order

1. Land documentation and template updates first.
2. Enable required CI checks for branch naming and PR source/base policy.
3. Enforce stricter push/auth policy for automation identities.
4. After one week, review failed-check metrics and adjust naming regex/model fallback order if needed.
