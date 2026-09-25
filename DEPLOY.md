# Start / stop the backend deployment

Quick reference for bringing the AWS backend up and tearing it down.
Full details: [`infra/README.md`](infra/README.md).

## Before the first start

1. **Images must exist in GHCR** — the host pulls them at boot. Either merge to
   `master` (`.github/workflows/images.yml` publishes them) or push by hand:
   ```bash
   GHCR_TOKEN=<pat-with-write:packages> ./deploy/push-images.sh
   ```
   Then either make the four packages **public**, or give the host a
   `read:packages` PAT at `backend/keys/ghcr-token.txt`.

2. **Config** — copy the example and set your frontend origin:
   ```bash
   cd infra
   cp terraform.tfvars.example terraform.tfvars
   $EDITOR terraform.tfvars      # cors_origins is required
   ```

3. **Secrets** — `infra/apply.sh` picks these up automatically, no need to export:
   - `backend/keys/openrouter-key.txt` (required)
   - `backend/keys/github-token.txt` (optional, for the git_commit executor)
   - `backend/keys/ghcr-token.txt` (optional, only if the packages are private)

## Start

```bash
cd infra
./apply.sh init      # first time only
./apply.sh apply     # ~2 min for AWS, then ~1-2 min for first boot on the host
```

Then:

```bash
tofu output -raw api_url          # backend HTTPS base URL
curl -s "$(tofu output -raw api_url)/node-types/" | head
```

Watch the host boot if something looks off:

```bash
aws ssm start-session --target "$(cd infra && tofu output -raw instance_id)"
sudo tail -f /var/log/cloud-init-output.log
```

Point the frontend at it: in Vercel set `NEXT_PUBLIC_API_BASE` to `api_url` and
**redeploy** (it is inlined into the bundle at build time).

## Check state

```bash
cd infra
./apply.sh output                                    # all outputs
aws ssm start-session --target "$(tofu output -raw instance_id)"

# on the host
cd /opt/agent-orch
sudo docker compose --env-file /mnt/data/agent-orch/backend.env ps
sudo docker compose --env-file /mnt/data/agent-orch/backend.env logs -f backend
```

## Stop without destroying

Containers only (data and infrastructure stay):

```bash
# on the host
cd /opt/agent-orch
sudo docker compose --env-file /mnt/data/agent-orch/backend.env stop
sudo docker compose --env-file /mnt/data/agent-orch/backend.env up -d   # start again
```

Stop the whole EC2 host (cheapest way to pause compute; EBS and the EIP keep
costing a little):

```bash
aws ec2 stop-instances --instance-ids "$(cd infra && tofu output -raw instance_id)"
aws ec2 start-instances --instance-ids "$(cd infra && tofu output -raw instance_id)"
```

Note: the Elastic IP stays associated, so it keeps its address.

## Destroy everything

```bash
cd infra
./apply.sh destroy
```

This deletes **the data volume too** — Postgres and every agent workspace are
gone, and the Elastic IP is released. Snapshot first if you might want it back:

```bash
VOL=$(aws ec2 describe-volumes \
  --filters "Name=tag:Name,Values=agent-orch-data" \
  --query 'Volumes[0].VolumeId' --output text)
aws ec2 create-snapshot --volume-id "$VOL" --description "agent-orch pre-destroy"
```

## Rough monthly cost (ap-south-1, idle)

| Item | Approx |
| --- | --- |
| m7i-flex.large on-demand | ~$63 |
| 80 GB gp3 (30 root + 50 data) | ~$7 |
| Elastic IP (attached) | $0 |
| **Total idle** | **~$70/mo** |

Costs rise with agent runs (agent containers, outbound data). To cut the idle
cost, `stop-instances` when you are not using it, or switch to a smaller type via
`instance_type` in `terraform.tfvars`.

### Free plan accounts

This account is on the AWS **FREE** plan, which only accepts free-tier-eligible
instance types — `t3.large` is rejected with *"The specified instance type is not
eligible for Free Tier"*. That is why `instance_type` defaults to
`m7i-flex.large` (2 vCPU / 8 GiB, x86_64). List what is allowed:

```bash
aws ec2 describe-instance-types \
  --filters Name=free-tier-eligible,Values=true \
  --query 'InstanceTypes[].{Type:InstanceType,MemMiB:MemoryInfo.SizeInMiB}' \
  --output table
```

Usage is drawn from the account's remaining credits (`aws freetier
get-account-plan-state`). Upgrading the account to a paid plan lifts the
restriction and lets you use `t3.large` / Graviton types.
