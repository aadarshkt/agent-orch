# Agent Orchestrator

Agent Orchestrator is a local workflow platform with a FastAPI backend and a Next.js frontend. Workflows can be created and imported from self-contained YAML definitions, then executed through registered agent executors with live events streamed over Server-Sent Events.

## Project structure

```text
agent-orch/
├── backend/       FastAPI API, database models, executors, registries, and YAML config
├── frontend/      Next.js web interface
├── deploy/        Backend image, host compose stack, Caddy TLS proxy, cloud-init bootstrap
├── infra/         OpenTofu config that provisions the AWS deployment
├── dev.sh         Starts PostgreSQL, the backend, and the frontend
├── architecture.txt
└── executor_onboarding.md
```

## Requirements

- Docker Desktop with the Docker daemon running
- Python 3.10 or newer
- Node.js 18 or newer
- npm

The development script starts PostgreSQL in Docker on port `5433`. The API runs on port `8010` and the frontend runs on port `3001`.

## Run the project

From the repository root:

```bash
chmod +x dev.sh
./dev.sh
```

The script will:

1. Start or reuse the `agent-orch-postgres` PostgreSQL container.
2. Create `backend/venv` and install `backend/requirements.txt`.
3. Create `backend/.env` from `backend/.env.example` when needed.
4. Start the FastAPI backend with reload enabled.
5. Install frontend dependencies when needed.
6. Start the Next.js development server.

Open these URLs after startup:

- Frontend: http://localhost:3001
- API: http://localhost:8010
- Interactive API documentation: http://localhost:8010/docs

Press `Ctrl+C` in the terminal running `dev.sh` to stop the backend and frontend. The PostgreSQL container is left running for reuse.

## Environment configuration

Backend defaults are defined in `backend/.env.example`:

```dotenv
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable
REGISTRY_USERNAME=
REGISTRY_PASSWORD=
REGISTRY_URL=
# CORS_ORIGINS=https://your-app.vercel.app   # required in production
# WORKSPACE_ROOT=/mnt/data/agent-orch/workspaces
```

Frontend defaults are defined in `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_BASE=http://localhost:8010
PORT=3001
```

Set `BACKEND_PORT` or `FRONTEND_PORT` when starting `dev.sh` to override the default ports:

```bash
BACKEND_PORT=8020 FRONTEND_PORT=3002 ./dev.sh
```

If you use an existing PostgreSQL instance instead of Docker, make sure it accepts connections on port `5433` or update `backend/.env` accordingly.

## API capabilities

The backend exposes routes for:

- `/node-types` — registered executor/node type definitions
- `/agents` — agent configuration management
- `/workflows` — workflow CRUD, YAML import, and execution
- `/runtimes` — configured runtime presets
- `/mcp-registry` — MCP registry operations
- `/events` — execution event streaming

The API initializes database tables, seeds built-in node types, loads runtime presets, and initializes the workflow checkpointer during startup.

## YAML workflow import

A workflow can be imported through `POST /workflows/import` using either a YAML file path or inline YAML content. The YAML definition can include runtimes, agents, workflow nodes, and edges in one self-contained document.

Use the interactive API documentation at `/docs` to inspect the request schema and try an import.

## Development checks

Backend Python sources can be compiled with:

```bash
cd backend
python3 -m compileall -q src
```

Frontend checks can be run with:

```bash
cd frontend
npm run lint
npm run build -- --webpack
```

The default `npm run build` uses Turbopack. On environments where Next.js native bindings are unavailable, use the Webpack command above.

## Deployment

The frontend is deployed on Vercel; the backend is provisioned with OpenTofu onto a single
AWS EC2 host that runs the API, PostgreSQL, a Caddy TLS proxy, and the ephemeral CLI-agent
containers in Docker.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set cors_origins
./apply.sh init && ./apply.sh apply
```

The `api_url` output is the backend's public HTTPS base URL — set it as
`NEXT_PUBLIC_API_BASE` in Vercel and redeploy. The host pulls its images (backend plus the
`test-cli`, `openrouter-agent`, and `aider-agent` runtimes) from GHCR, published by
`.github/workflows/images.yml` or `deploy/push-images.sh`.

See [`infra/README.md`](infra/README.md) for the full runbook: what gets created, the
verification checklist, operating procedures, and the known gaps (notably: there is no auth
yet).

## Troubleshooting

### Docker daemon is unavailable

If `./dev.sh` fails with `Cannot connect to the Docker daemon`, start Docker Desktop and run the script again. PostgreSQL is required for backend startup.

### Frontend native binding errors

If Next.js reports that Turbopack native bindings are unavailable, build with Webpack:

```bash
cd frontend
npm run build -- --webpack
```

### Backend dependency installation

The startup script creates and uses `backend/venv`. To reset the environment, remove that directory and run `./dev.sh` again.
