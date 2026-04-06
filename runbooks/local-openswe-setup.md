# Runbook: Local Open SWE Setup

Run the full Open SWE stack locally with Docker sandboxes and Open WebUI frontend.

## Prerequisites

- Docker Desktop running
- Python 3.11-3.13 with uv
- DevPod CLI installed
- The fork at `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra` with venv set up (`uv sync --all-extras`)
- API keys: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `TOKEN_ENCRYPTION_KEY`

Generate the encryption key if you don't have one:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 1. Install DevPod Docker Provider

```bash
devpod provider add docker
```

## 2. Export Environment Variables

In the terminal where you'll run aegra:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...
export TOKEN_ENCRYPTION_KEY=...
export SANDBOX_TYPE=devpod
export DEVPOD_PROVIDER=docker
export LANGGRAPH_URL=http://localhost:2026
```

Aegra's docker-compose creates Postgres with non-default credentials. You must also set:

```bash
export POSTGRES_USER=open_swe_aws_devpod_aegra
export POSTGRES_PASSWORD=open_swe_aws_devpod_aegra_secret
export POSTGRES_DB=open_swe_aws_devpod_aegra
```

(These match the defaults in the auto-generated `docker-compose.yml`. If the directory name differs on another machine, aegra will generate different defaults -- check the compose file.)

## 3. Start Aegra

```bash
cd /Users/wsmoak/Projects/open-swe-aws-devpod-aegra
source .venv/bin/activate
aegra dev
```

This will:
- Generate a `docker-compose.yml` in the repo root
- Start a Postgres container (pgvector/pgvector:pg18)
- Run database migrations
- Start the Aegra server on port 2026 with `--reload`

**Important:** Do NOT have another Postgres container on port 5432 when you run this. If port 5432 is taken, the Postgres container will start but the port won't be published to the host, and aegra will fail to connect.

Verify:
```bash
curl -s http://localhost:2026/health
# {"status":"healthy"}
```

## 4. Test Without UI

From any terminal with the aegra venv activated:

```bash
python /Users/wsmoak/Projects/open-swe-aws-devpod-project/scripts/test_local_aegra.py
```

This creates a thread, sends a message, and prints the agent's response. The first run will be slow (DevPod creates a Docker sandbox container, clones the repo).

## 5. Start Open WebUI

```bash
docker run -d --name open-webui \
  -p 8080:8080 \
  -v open-webui-data:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

First startup is slow -- it downloads embedding models from Hugging Face. Wait until `http://localhost:8080` loads. Use a different port if 8080 is taken.

## 6. Start Pipelines Server

```bash
docker run -d --name pipelines \
  -p 9099:9099 \
  -v pipelines-data:/app/pipelines \
  ghcr.io/open-webui/pipelines:main
```

## 7. Install the Pipe Function

```bash
docker cp /Users/wsmoak/Projects/open-swe-aws-devpod-project/scripts/openwebui_aegra_pipe.py \
  pipelines:/app/pipelines/openwebui_aegra_pipe.py
docker restart pipelines
```

Verify it loaded:
```bash
docker logs pipelines 2>&1 | grep "Loaded module"
# INFO:root:Loaded module: openwebui_aegra_pipe
```

## 8. Connect Open WebUI to Pipelines

1. Open `http://localhost:8080`
2. Create an admin account (first user becomes admin)
3. Go to **Admin Panel -> Settings -> Connections**
4. Add an **OpenAI API** connection:
   - URL: `http://host.docker.internal:9099`
   - API Key: `0p3n-w3bu!` (default pipelines key)
5. Save and verify the connection

## 9. Use It

1. Start a new chat in Open WebUI
2. Select **Open SWE** from the model dropdown
3. Send a message -- the agent will create a Docker sandbox and work in it

The pipe function defaults to `wsmoak/rails-otel-demo`. To change the target repo, go to **Admin Panel -> Settings -> Pipelines** and edit the valve values for `DEFAULT_REPO_OWNER` and `DEFAULT_REPO_NAME`.

## Troubleshooting

### Aegra can't connect to Postgres
- Check nothing else is on port 5432: `lsof -i :5432`
- Tear down and recreate: `docker compose -f /path/to/docker-compose.yml down && docker compose -f /path/to/docker-compose.yml up -d --wait postgres`
- Verify port is published: `docker port <container-name>` should show `5432/tcp -> 0.0.0.0:5432`

### "Bot-token-only mode" / GitHub App not configured
- This was fixed in commit `4594213f` -- `_resolve_bot_installation_token` now falls back to `GITHUB_TOKEN` env var
- Make sure `GITHUB_TOKEN` is exported in the terminal running `aegra dev`

### get_config NameError
- This was fixed in commit `4594213f` -- `get_config()` replaced with `config.get()` in `server.py:459`
- If you merge from upstream main, check that this line hasn't reverted

### "Pipelines Not Detected" in Open WebUI
- Verify pipelines is running: `curl -s http://localhost:9099/` should return `{"status":true}`
- Verify Open WebUI can reach it: `docker exec open-webui curl -s http://host.docker.internal:9099/`
- The URL in Connections must be `http://host.docker.internal:9099` (not localhost, since Open WebUI runs in Docker)

### Pipeline shows "No Pipeline class found"
- The class must be named `Pipeline`, not `Pipe`
- The `pipe()` method signature must be: `pipe(self, user_message, model_id, messages, body)`

### TransferEncodingError in Open WebUI
- Usually means the pipe function crashed mid-stream
- Check `docker logs pipelines` for the actual Python traceback

## Cleanup

Stop everything:
```bash
docker stop open-webui pipelines && docker rm open-webui pipelines
docker compose -f /path/to/open-swe-aws-devpod-aegra/docker-compose.yml down
# Stop aegra dev with Ctrl+C
```

Docker sandbox containers created by DevPod persist. List them:
```bash
docker ps -a --filter "label=devpod"
```
