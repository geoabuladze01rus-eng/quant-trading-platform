# Paper database backup and recovery

The persistent paper account is local, hypothetical state. Back it up before a
long-running paper session and before any manual database maintenance. Keep backup
files outside the Git checkout and restrict access to the account owner.

## Create and verify a backup

The backup command uses SQLite's online backup API, so it produces a consistent
snapshot even when SQLite is using WAL. It checks the copied database's integrity
and required ledger tables, writes the file with owner-only permissions, and refuses
to overwrite an existing path.

```bash
python -m quant_trading_platform.persistence.backup backup \
  --database data/paper_alpha.sqlite3 \
  --output "$HOME/quant-backups/paper-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"

python -m quant_trading_platform.persistence.backup verify \
  "$HOME/quant-backups/paper-YYYYMMDDTHHMMSSZ.sqlite3"
```

Replace the example verification filename with the exact path printed by the backup
command. Keep backups on a separate disk or protected storage and define a retention
policy before collecting valuable test history.

## Recovery procedure

There is no automated restore command yet. To recover, stop the API/container first,
preserve the current database as a separate file, copy the verified backup to the
configured `PAPER_DATABASE_PATH`, then start the application and inspect
`/paper/reconciliation` and `/paper/account`. Startup reconciliation must report a
clean account before continuing a paper session. Never overwrite the only copy of a
paper ledger.

This is an operator procedure, not a production disaster-recovery guarantee. Restore
and backup drills on the actual deployment environment remain a prerequisite for
long-running unattended paper tests.
