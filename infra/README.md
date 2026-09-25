# Backend deployment (AWS EC2 + OpenTofu)

Provisions a single EC2 host that runs the whole backend stack in Docker:

```
Internet ──HTTPS──> Caddy (Let's Encrypt, SSE pass-through)
                       │
                       └─> backend (FastAPI/LangGraph, 1 uvicorn worker)
                             │        │
                             │        └─> postgres:16        (data volume)
                             └─ /var/run/docker.sock ──> ephemeral CLI-agent
                                                          containers
```

The frontend stays on Vercel and talks to this host directly over HTTPS (browser
`fetch` + `EventSource`), so `NEXT_PUBLIC_API_BASE` points at the `api_url` output.

## Why one VM (and not ECS/Lambda/App Runner)

The `cli_agent` executor shells out to the host's `docker` CLI to start ephemeral
agent containers (`docker run --rm -v <node_dir>:/workspace`, see
`backend/src/executors/cli_agent_executor.py:282`). That needs a real Docker daemon,
so Fargate-class runtimes are out. It also must run as **one** process: the SSE event
bus is in-process (`backend/src/engine/pubsub.py`), so multiple workers or replicas
would not see each other's events.

## Prerequisites

- OpenTofu >= 1.6 (`brew install opentofu`)
- AWS credentials with permission to create EC2/EBS/EIP/IAM/SSM/Route53 resources
- Docker locally (only if you push images by hand)
- The images must exist in GHCR before the host boots — either merge to `master` so
  `.github/workflows/images.yml` publishes them, or:

  ```bash
  GHCR_TOKEN=<pat-with-write:packages> ./deploy/push-images.sh
  ```

  Then either make the four GHCR packages **public**, or give `ghcr_token` a PAT with
  `read:packages` so the host can pull them.

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — cors_origins is required (your Vercel origin)

./apply.sh init
./apply.sh plan
./apply.sh apply
```

`apply.sh` injects the secrets as `TF_VAR_*` from local files, so they never appear in
`terraform.tfvars`, the shell history, or a shared state file:

| Secret             | Source (first match wins)                                  |
| ------------------ | ---------------------------------------------------------- |
| `OPENROUTER_API_KEY` | `backend/keys/openrouter-key.txt` or `backend/.env`      |
| `GITHUB_TOKEN`     | `backend/keys/github-token.txt` or `backend/.env` (optional) |
| `GHCR_TOKEN`       | `backend/keys/ghcr-token.txt` or `$GHCR_TOKEN` (optional)   |

Secrets are stored as SSM `SecureString` parameters and read **by the host at boot**
(`deploy/bootstrap.sh`), not baked into `user_data` — so they are not exposed via the
instance metadata service or the EC2 console.

## What gets created

| Resource | Notes |
| --- | --- |
| EC2 instance (Ubuntu 24.04, `t3.large` by default) | runs the compose stack via cloud-init |
| gp3 root volume (30 GB) | OS + images + container layers |
| gp3 data volume (50 GB) | mounted at `/mnt/data`: Postgres, workspaces, ACME certs |
| Elastic IP | stable address for DNS / `NEXT_PUBLIC_API_BASE` |
| Security group | 80/443 open to the world; 22 only if `key_name` + `ssh_cidr` are set |
| IAM role | SSM Session Manager (shell without SSH) + read of this project's SSM params |
| SSM parameters | `postgres_password`, `openrouter_api_key`, optional tokens |
| Route53 A record | only when `domain` + `route53_zone_id` are set |

On the host:

```
/opt/agent-orch/{docker-compose.yml,Caddyfile,bootstrap.sh,deploy.conf}
/mnt/data/agent-orch/{backend.env,pgdata/,workspaces/,caddy/,\.docker/}
```

## After apply

1. Watch first boot (takes a couple of minutes):
   ```bash
   aws ssm start-session --target $(tofu output -raw instance_id)
   sudo tail -f /var/log/cloud-init-output.log
   ```
2. Smoke-test the API:
   ```bash
   curl -s "$(tofu output -raw api_url)/node-types/" | head
   curl -s "$(tofu output -raw api_url)/runtimes/" | head
   ```
3. Point the frontend at it: in Vercel → Settings → Environment Variables set
   `NEXT_PUBLIC_API_BASE=$(tofu output -raw api_url)` and **redeploy** (it is inlined
   into the client bundle at build time).
4. Run a workflow in the UI with a `test-cli` runtime node and watch the live log
   stream in the dashboard.

The default is `<elastic-ip>.sslip.io`, which resolves to the instance and still gets a
valid Let's Encrypt certificate — so this works before you own a domain. To move to a
real hostname later, set `domain`/`route53_zone_id` and re-apply.

## Verification checklist

- `docker compose --env-file /mnt/data/agent-orch/backend.env -f /opt/agent-orch/docker-compose.yml ps`
  → `postgres` healthy, `backend` and `caddy` up.
- `docker logs agent-orch-backend-1` → "Database tables initialized." and
  "Initialized PostgresSaver checkpointer successfully." (absent DB = the app silently
  falls back to MemorySaver).
- `curl -N "$(tofu output -raw api_url)/events/stream?thread_id=probe"` → a
  `{"event": "ping"}` line within ~15 s (proves Caddy is not buffering SSE).
- During an agent run, on the host: `docker ps` shows the ephemeral container, and its
  mounts resolve to `/mnt/data/agent-orch/workspaces/agent-orch/<thread>/<node>` — the
  same path inside the backend container.
- CORS: `curl -si -H "Origin: <your-vercel-origin>" "$(tofu output -raw api_url)/workflows/" | grep -i access-control-allow-origin`.

## Operating it

**Ship a new backend image** (after CI pushes it):

```bash
# on the host
cd /opt/agent-orch
sudo docker compose --env-file /mnt/data/agent-orch/backend.env pull backend
sudo docker compose --env-file /mnt/data/agent-orch/backend.env up -d backend
```

**Re-run provisioning** (after editing `deploy/`, or to retry a failed boot):

```bash
# on the host
sudo bash /opt/agent-orch/bootstrap.sh
```

**Change domain / CORS / secrets** → edit `terraform.tfvars` or the secret files and
`./apply.sh apply`. Note `user_data` embeds the domain, so a domain change replaces the
instance (the data volume and its Postgres/workspaces persist).

**Teardown:** `./apply.sh destroy`. This destroys the data volume too — Postgres and all
workspaces are gone. Back up first if you care:
`aws ec2 create-snapshot --volume-id <id>`.

## Troubleshooting

- **Cloud-init failed** → `sudo cat /var/log/cloud-init-output.log`. Re-run with
  `sudo bash /opt/agent-orch/bootstrap.sh`.
- **Data volume not mounted** → `lsblk`; the script waits 60 s for
  `/dev/nvme1n1` (Nitro) or `/dev/xvdf`. `sudo mount -a` after attaching manually.
- **"no basic auth credentials" / pull denied** → the GHCR packages are private and
  `ghcr_token` is unset. Set it and re-apply, or make the packages public.
- **Agent node fails immediately** → check the runtime's `required_env` is present in
  `/mnt/data/agent-orch/backend.env` (e.g. `OPENROUTER_API_KEY`), and that the image
  name in `backend/config/runtimes.yaml` actually exists in GHCR.
- **Browser gets a CORS error** → the exact origin (scheme + host + port, no trailing
  slash) must be in `cors_origins`. Vercel preview deployments use a different hostname
  per deployment.
- **"The specified instance type is not eligible for Free Tier"** → the account is on the
  AWS FREE plan, which blocks most types. Pick one that is free-tier-eligible:
  ```bash
  aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true \
    --query 'InstanceTypes[].{Type:InstanceType,MemMiB:MemoryInfo.SizeInMiB}' --output table
  ```
  `m7i-flex.large` (2 vCPU / 8 GiB) is the default for this reason.
- **Live logs never appear but the run finishes** → something in front of the backend is
  buffering; the `flush_interval -1` in `deploy/Caddyfile` is what prevents that.

## Known gaps (deliberately not addressed)

- **No auth.** The API is public; CORS is not authorization, and it can spawn containers
  on the host. Fine for a single-tenant POC — put an API key or an authenticating proxy
  in front before this goes wider.
- **Docker socket = root-equivalent.** Anything reaching the API can effectively become
  root on the VM.
- **Single instance, no HA.** Downtime during instance replacement; agents run on the app
  host, so a heavy run competes with the API and Postgres.
- **No schema migrations.** The app uses `create_all` only, so changes to existing tables
  will not apply on restart.
- **Workspaces are not off-box durable.** They live on the EBS volume; nothing is pushed
  to S3, and the frontend cannot browse artifacts.
- **`beacon` / `claude-cli` / `python-agent` runtimes** reference `ghcr.io/eco/*` images
  that do not exist — those nodes will fail to pull.
