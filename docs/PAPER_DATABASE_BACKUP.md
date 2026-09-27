# Paper database backup and restore runbook

The persistent paper account is local, hypothetical state. These commands never
contact an exchange and never enable live trading. Keep snapshots outside the Git
checkout, restrict them to the account owner, and use unique filenames: backup and
recovery files are never silently overwritten.

The utility uses SQLite's online backup API, so a backup includes committed WAL
content in one standalone SQLite file. Verification runs `integrity_check`,
`foreign_key_check`, and the canonical table/column/index/schema-version contract.

## 1. Create and verify a backup

```bash
python -m quant_trading_platform.persistence.backup backup \
  --database data/paper_alpha.sqlite3 \
  --output /absolute/protected/path/paper-YYYYMMDDTHHMMSSZ.sqlite3

python -m quant_trading_platform.persistence.backup verify \
  --standalone /absolute/protected/path/paper-YYYYMMDDTHHMMSSZ.sqlite3
```

Keep a separate retention policy and protected storage. This repository does not
schedule daily snapshots, encrypt backup storage, or test the operator's disk.

## 2. Stop the service

Stop every API process or container that uses `PAPER_DATABASE_PATH`. For Compose,
run `docker compose down` without `-v`; for a local Uvicorn process, stop it and wait
for exit. Do not run restore against an open, production, user, or shared database.
The restore command also attempts an exclusive lock and WAL checkpoint and fails
closed if a writer is active, but that check is not a substitute for stopping the
service.

## 3. Restore with explicit confirmation

Choose a new, non-existing recovery-backup filename. The command verifies the
restore source first, stages and migrates a private copy, checks that the target is
idle, creates and verifies a snapshot of the current target, checkpoints committed
WAL data, and finally installs the staged database with an atomic same-directory
replacement.

```bash
python -m quant_trading_platform.persistence.backup restore \
  --backup /absolute/protected/path/paper-YYYYMMDDTHHMMSSZ.sqlite3 \
  --database data/paper_alpha.sqlite3 \
  --recovery-backup /absolute/protected/path/pre-restore-YYYYMMDDTHHMMSSZ.sqlite3 \
  --confirm-replace
```

Without `--confirm-replace`, an existing target is never replaced. If validation,
locking, staging, backup, or atomic replacement fails, the command exits non-zero.
Before replacement, the original main file remains usable; after replacement has
started, the verified recovery snapshot is used for best-effort automatic rollback.
Never delete that recovery snapshot until the post-restore checks pass.

## 4. Restart and verify

Start the local service, then verify all of the following before resuming paper
commands:

```bash
python -m quant_trading_platform.persistence.backup verify data/paper_alpha.sqlite3
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/paper/reconciliation
curl --fail 'http://127.0.0.1:8000/audit?limit=20&offset=0'
```

`/health` must report `trading_mode=paper` and `live_trading=locked`. The verifier
must report the current schema version and `integrity=ok`; reconciliation must be
`ok`; startup recovery/audit records must be present and explainable. If any check
fails, stop the service again, preserve all database/recovery files, and investigate.
Do not repeatedly restore over the evidence and do not resume the paper session.

## Readiness limitation

Unit tests perform migrations and restore drills only on temporary databases. A
successful drill in the actual local deployment environment, automated daily
snapshots with retention monitoring, and feed-freshness soak observation are still
required before long-running unattended paper testing. This is not a production
disaster-recovery guarantee.
