# Research Monitor v0.1.0 Release Evidence

This file records the reproducible release-candidate checks run on 2026-07-16. A passing row means that the stated command class completed successfully in the recorded environment; it is not a certification of environments that were not exercised.

| Area | Reproducible command or scenario | Recorded result |
| --- | --- | --- |
| Backend | `uv run pytest tests/backend` | 220 passed |
| Frontend unit/component | `cd frontend && npm test -- --run` | 87 passed |
| Browser end-to-end | `cd frontend && npm run test:e2e` | 5 passed against the production FastAPI/Vite bundle |
| Companion skill | `uv run pytest tests/skill` | 9 passed |
| NFS recovery | Install the built wheel into an isolated monitor home located on the actual NFS mount, then create a backup, apply a monitor-only mutation, and restore the backup | Passed; restored integrity, schema, and pre-mutation state were verified |
| Writer exclusion | Start/hold the database-adjacent writer lock, then access the same data directory through a distinct runtime directory | Passed closed with `shared_writer_active` before database access |
| Distribution contents | `uv build`, then inspect both the sdist and wheel with `tests/backend/test_installed_wheel.py` | Python, frontend, skill, and MIT license inclusion are validated by the release test |

The final wheel and sdist SHA-256 digests belong in the release record produced
*after* the tagged source tree is built. They are intentionally not embedded in
this source file: changing this file would change both artifacts and invalidate
an embedded digest. A release is not complete until its external release record
identifies the source tag/commit, build-tool versions, artifact names, and
digests.

## Scope limits

- The crash, backup, mutation, and restore exercise used a real NFS filesystem from one host. It did not use a second host.
- The writer-lock exercise used separate runtime directories on one host. Cross-host coordination therefore remains conditional on the NFS server and client mounts honoring advisory locks.
- The current NFS mount reports `soft,local_lock=none`; this release does not certify that mount for a shared two-host database without an operator-run, server-side lock verification.
- No deployment-level `kill -9` test was claimed. Subprocess hot-journal recovery is covered by the backend tests, but that is narrower than forcibly terminating an installed production server during a live mutation.
