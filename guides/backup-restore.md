# Backup & restore (self-host)

Your instance keeps everything in one place: the Postgres database (`db` service).
Encrypted BYO provider keys, broker credentials, runs, backtests, portfolios,
notifications — all of it is rows in Postgres. Backing up = dumping that database;
restoring = loading a dump into a fresh database. No external vendor, no S3, no
WAL shipping. Redis holds only transient Celery/broker state and cache, so it does
**not** need backing up.

> One thing to keep with the dump: your `.env` — specifically `FIELD_ENCRYPTION_KEY`
> (and `DJANGO_SECRET_KEY`/`JWT_SIGNING_KEY`). BYO keys and broker credentials are
> encrypted at rest with `FIELD_ENCRYPTION_KEY`; a database restored without the
> matching key can be read, but those encrypted columns are unrecoverable. Store
> the key separately from the dump.

All commands below assume the compose stack is running
(`docker compose -f infra/docker-compose.yml …`) and the default credentials from
`.env.example` (`POSTGRES_USER=hedgefund`, `POSTGRES_DB=hedgefund`).

## Back up

Custom-format (`-Fc`) dump — compressed and restorable with `pg_restore`:

```bash
docker compose -f infra/docker-compose.yml exec -T db \
  pg_dump -U hedgefund -Fc hedgefund > hf_backup_$(date +%F).dump
```

Keep the `.dump` file and a copy of `FIELD_ENCRYPTION_KEY` somewhere safe (and
off the machine). That's the whole backup.

## Restore into a scratch instance (verify before you trust it)

Never restore straight over your live database. Restore into a **scratch**
database first, sanity-check it, then decide.

```bash
# 1. Create an empty scratch database.
docker compose -f infra/docker-compose.yml exec -T db \
  createdb -U hedgefund hf_restore_test

# 2. Load the dump into it.
docker compose -f infra/docker-compose.yml exec -T db \
  pg_restore -U hedgefund -d hf_restore_test --no-owner < hf_backup_YYYY-MM-DD.dump

# 3. Row-count sanity — the restored counts should match the source.
for t in runs_run data_newsitem hedgefund_agents_llmcall; do
  docker compose -f infra/docker-compose.yml exec -T db \
    psql -U hedgefund -d hf_restore_test -tAc "select '$t', count(*) from $t"
done

# 4. Drop the scratch DB when you're done checking.
docker compose -f infra/docker-compose.yml exec -T db \
  dropdb -U hedgefund hf_restore_test
```

To restore **for real** (disaster recovery), stop the app, drop and recreate the
`hedgefund` database, restore into it, and bring the app back up:

```bash
docker compose -f infra/docker-compose.yml stop web worker beat
docker compose -f infra/docker-compose.yml exec -T db dropdb -U hedgefund hedgefund
docker compose -f infra/docker-compose.yml exec -T db createdb -U hedgefund hedgefund
docker compose -f infra/docker-compose.yml exec -T db \
  pg_restore -U hedgefund -d hedgefund --no-owner < hf_backup_YYYY-MM-DD.dump
docker compose -f infra/docker-compose.yml up -d web worker beat
```

## Backing up a cloud-hosted database

If you run the stack in the cloud (see [`railway-deploy.md`](./railway-deploy.md)), the
managed Postgres is now your source of truth — the same `-Fc` dump/restore flow works
against it from your machine over its public TCP-proxy URL. Two differences:

- **Match the client to the server major version.** Run `pg_dump`/`pg_restore` from a
  `postgres:<major>` image matching the managed server (check its image tag), not
  whatever your local `db` service happens to be. A `-Fc` dump taken by an older
  `pg_dump` restores into a newer server fine, but the *restore client* must match.
- **There's no `exec db`** — point the client at the connection URL instead:

```bash
# Back up the cloud database into the same backups/ flow.
docker run --rm -v "$PWD/backups:/b" postgres:<major> \
  pg_dump -Fc "<DATABASE_PUBLIC_URL>" > backups/cloud_$(date +%F).dump

# Restore a dump into it (e.g. migrating a local instance up to the cloud).
docker run --rm -v "$PWD/backups:/b" postgres:<major> \
  pg_restore --no-owner --no-privileges -d "<DATABASE_PUBLIC_URL>" /b/<file>.dump
```

Keep `FIELD_ENCRYPTION_KEY` with these dumps exactly as you would locally — it is what
makes the encrypted provider/broker columns readable, and a cloud instance uses its own
`DJANGO_SECRET_KEY`. Also enable your provider's native database backups if the plan
offers them; treat them as a complement to these dumps, not a replacement.

## Smoke test after a restore

1. **Health probe** — `curl -s -o /dev/null -w '%{http_code}' http://localhost:8811/api/health/`
   should print `200`.
2. **Row-count sanity** — the counts from step 3 above should match the source
   database (or your last known totals).
3. **Sign in** and open the fund/runs pages — data you expect should be there.

## Wall-clock (measured once, end-to-end)

On a developer laptop against a real dev database (**520 runs, ~277k news items,
~101k LLM-call rows; 143 MB custom-format dump**):

| Step | Wall-clock |
| --- | --- |
| `pg_dump -Fc` | ~20 s |
| `pg_restore` into scratch DB | ~40 s |
| Row-count sanity + health probe | < 5 s |

Your numbers scale with database size; treat these as an order-of-magnitude
reference, not a guarantee.

## Optional: a cron sidecar

To take a nightly dump without a scheduler on the host, add a tiny sidecar to your
compose (keeps the 14 most recent dumps in a named volume):

```yaml
  db-backup:
    image: postgres:16
    depends_on: [db]
    environment:
      PGPASSWORD: hedgefund
    volumes:
      - dbdumps:/dumps
    entrypoint: ["/bin/sh", "-c"]
    command:
      - >
        while true; do
          pg_dump -h db -U hedgefund -Fc hedgefund
            > /dumps/hf_$(date +%F_%H%M).dump;
          ls -1t /dumps/*.dump | tail -n +15 | xargs -r rm -f;
          sleep 86400;
        done

volumes:
  dbdumps:
```

This is deliberately minimal — no rotation policy beyond "keep 14", no off-machine
copy. For anything you actually care about, copy the dumps somewhere off the host.
