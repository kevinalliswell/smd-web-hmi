# Commercial Readiness Closure Plan

## Objective

Turn the latest `dev` baseline into a release candidate that is suitable for commercial deployment. The release branch targets `dev`; `main` remains untouched. The only planned acceptance item left to the owner is real Windows hardware/firmware verification.

## Release target

- Working branch: `release/commercial-readiness`
- Integration target: `dev`
- Candidate version: `0.3.0-rc.1`
- Final tag: created only after owner hardware sign-off

## Phase 1 — Security and session integrity

1. Add regression tests for redacted validation errors, security headers, production API-doc policy, and request correlation.
2. Raise PBKDF2-HMAC-SHA256 work factor to the current project baseline and transparently rehash legacy passwords after successful login.
3. Add a per-user token version migration; validate current account state on authenticated REST and WebSocket requests; revoke outstanding tokens on logout, password change/reset, role change, and deactivation.
4. Replace WebSocket query-string JWT authentication with an initial authentication message, validate Origin, cap message size/rate, enforce idle timeout, and revalidate the account session.
5. Move browser tokens from persistent local storage to session storage and wait for WebSocket authentication acknowledgement before reporting connectivity.

## Phase 2 — Business data and reporting

1. Persist original specimen height and start-test metadata in `TestSession`; collect them at test start and keep device command payloads protocol-safe.
2. Make reports and analytics use stored specimen height, with an explicit compatibility override for legacy data.
3. Move analytics aggregation to SQL and add indexes for test, alarm, audit, parameter snapshot, and report access paths.
4. Implement honest HTML, PDF, and XLSX report exports, MIME-safe downloads, and format-selection tests/UI.
5. Add pagination/bounds where operational lists can grow without limit.

## Phase 3 — Production operations

1. Fail closed in production when the database schema is not at the Alembic head; expose meaningful liveness/readiness checks for database, schema, storage, host communication, and backup state.
2. Add SQLite online backup with retention and export cleanup, including an operator-triggerable path and observable status.
3. Complete mock-host `disconnect_after` fault injection and its deterministic tests.
4. Use production JSON logs with request IDs while retaining readable development logs.
5. Harden Windows install/upgrade/start scripts, add unattended health/package smoke checks and scheduled-task registration, and exercise the package path in CI.

## Phase 4 — Release closure

1. Synchronize backend/frontend versioning at `0.3.0-rc.1` and update the changelog.
2. Refresh README, D4 readiness, release/maintenance guide, interface checklist, and a concise owner hardware sign-off checklist.
3. Remove or explicitly retain audited dead compatibility modules after owner direction.
4. Close every software-only readiness item; keep supplier protocol confirmation and physical-machine measurements explicitly marked as external acceptance work.

## Verification gates

- Backend: full tests, coverage at least 80%, Black, isort, Ruff/static checks where configured.
- Frontend: lint, unit tests, production build, dependency audit.
- Data: migrate an empty database to head and verify upgrade from the prior head.
- Security: Python and npm dependency audits; auth/session regression suite.
- Runtime: browser smoke, responsive/accessibility, console/network checks.
- Packaging: Windows/offline package smoke and release workflow green.
- Delivery: code review, pull request into `dev`, CI green, merge into `dev`; no `main` mutation and no final release tag before hardware sign-off.

## Rollback strategy

Each phase is committed independently. Schema changes are additive where possible; operational scripts create verified backups before mutation. The release branch can be abandoned without changing `dev`, and the final merge can be reverted phase-by-phase if a gate fails.
