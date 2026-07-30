# Self-Host Deployment

MemPhant self-hosting is one regional cell: `memphant-server`, `memphant-worker`, Postgres with pgvector, and a customer-owned object store. The Docker path is for local validation and small deployments; the provider profile is the production gate.

## Local Compose

```bash
docker compose up --build --wait
curl -fsS http://127.0.0.1:3000/v1/health
cargo run -p memphant-cli -- db bootstrap-check --provider plain-postgres
```

The Compose stack uses `pgvector/pgvector:0.8.4-pg17`, waits for Postgres with `service_healthy`, and binds both Postgres and HTTP to localhost. It does not expose a browser-facing database role. A one-shot `bootstrap` service applies the bundled migrations and provisions the served login roles; `server` and `worker` wait for it with `service_completed_successfully`. The Postgres superuser is the migrator credential only — the services connect as `memphant_app_login` / `memphant_authn_login` / `memphant_worker_login`.

## Served Roles (do not skip)

Every tenant-scoped table carries `ENABLE` + `FORCE ROW LEVEL SECURITY` and a `_tenant_isolation` policy keyed on `memphant.current_tenant_id()`. Those policies do nothing for a connection whose effective role is `SUPERUSER` or `BYPASSRLS` — including `memphant_owner`, which holds a `using(true)` owner policy on every table. **A deployment that serves traffic with any such credential has no database-enforced tenant isolation.**

The shape that works:

1. Apply migrations with the migrator/superuser credential. `20260703_001` creates the six NOLOGIN, NOINHERIT capability roles (`memphant_owner`, `memphant_app`, `memphant_worker`, `memphant_authn`, `memphant_readonly`, `memphant_provisioner`); `20260730_004` adds one NOINHERIT **login** role per served capability, each a member of exactly one capability role and passwordless.
2. Give those login roles credentials:

   ```bash
   bash scripts/provision_login_roles.sh "$DATABASE_URL"   # prints MEMPHANT_*_DATABASE_URL lines
   ```

   Managed providers that authenticate by IAM grant membership instead: `grant memphant_app to <iam_login>;`.
3. Point `MEMPHANT_APP_DATABASE_URL`, `MEMPHANT_AUTHN_DATABASE_URL` and `MEMPHANT_WORKER_DATABASE_URL` at those credentials. `DATABASE_URL` is rejected by the server and worker.

Because the capability roles are NOINHERIT, each pooled connection issues an explicit `SET ROLE` at connect time, and the store then **refuses to serve** unless the effective role is exactly that capability role and is neither `SUPERUSER` nor `BYPASSRLS`. `memphant db bootstrap-check` applies the same rule to the profile before a maintenance window.

## Production Profile

Copy `deploy/provider-profiles/plain-postgres.env.example`, replace the placeholders, then run:

```bash
cargo run -p memphant-cli -- db bootstrap-check --provider plain-postgres --profile /path/to/plain-postgres.env
```

The check must pass before `memphant db bootstrap` is allowed in a maintenance window. It verifies the bundled migration boundary, the `memphant` schema name, a Postgres URL, region alignment between Postgres and object store, object-store versioning, and an object retention window at least one day longer than the Postgres PITR window.

## Required Postgres Shape

- Postgres 17 or 18.
- `vector` pgvector 0.8.x, `pg_trgm`, `ltree`, and `btree_gist`.
- A migrator role able to create objects in `memphant` and to `CREATE ROLE`.
- Non-superuser, non-BYPASSRLS login roles for the served processes (see above).
- No references to `public`, `syndai`, provider auth schemas, or browser roles from MemPhant migrations.
- RLS and tenant-prefixed indexes on tenant-scoped tables.

