# OpenSWE Migration from LangGraph Platform to Aegra

**Date:** 2026-04-04

## Background

OpenSWE was originally written for LangGraph Platform, a hosted service from LangChain
that runs LangGraph agents. We are replacing it with Aegra, an open-source compatible
alternative that we self-host on ECS.

Both LangGraph Platform and Aegra:
- Expose a REST API for creating/managing runs and threads
- Load your agent code and execute it when a run is triggered
- Handle persistence (threads, checkpointing) in Postgres

## Architecture

```
agent/
  server.py     ← defines get_agent() - the LangGraph graph factory
  webapp.py     ← FastAPI app with GitHub/Slack webhook handlers
  tools/        ← tools the agent uses (git, GitHub, DevPod, etc.)
  utils/        ← auth, sandbox helpers, etc.
aegra.json      ← tells Aegra where to find server.py and webapp.py
```

When a GitHub webhook arrives, `webapp.py` handles it, creates a thread/run via the
Aegra API, and Aegra calls `get_agent()` from `server.py` to get the graph to execute.

## Issues Found and Fixed

### 1. Wrong graph path format in aegra.json

`aegra.json` had the graph path in Python module syntax:

```json
"agent": "agent.server:get_agent"
```

Aegra expects a file path:

```json
"agent": "./agent/server.py:get_agent"
```

This caused: `ValueError: Graph file not found: /app/agent.server`

**Fix:** Updated `aegra.json` to use the file path format.

### 2. Relative imports in server.py broke under Aegra

`server.py` used relative imports (`from .middleware import ...`, `from .prompt import ...`, etc.).

When Aegra loads `server.py` via `importlib.util.spec_from_file_location`, it registers
the module as `graphs.agent` rather than `agent.server`. Relative imports from `.` then
try to resolve against `graphs` which doesn't exist.

This caused: `ModuleNotFoundError: No module named 'graphs'`

**Fix:** Changed all relative imports in `server.py` to absolute imports (`from agent.middleware import ...`, etc.). The other files in the `agent` package were unaffected because they are loaded through normal Python package machinery once `agent` is imported.

### 3. LangGraph Platform's __is_for_execution__ flag not set by Aegra

`get_agent()` had this guard:

```python
if thread_id is None or not graph_loaded_for_execution(config):
    logger.info("No thread_id or not for execution, returning agent without sandbox")
    return create_deep_agent(system_prompt="", tools=[])
```

`graph_loaded_for_execution()` checks for `__is_for_execution__` in the config
configurable dict.

**Why this existed:** LangGraph Platform calls `get_agent()` in two contexts:
- **Schema introspection** at startup — just to extract input/output schema for the API.
  No thread, no real execution. You don't want a DevPod sandbox created here.
- **Actual execution** — sets `__is_for_execution__: True` to signal a real run.

**Why it broke under Aegra:** Aegra never sets `__is_for_execution__`. It treats
`get_agent` as a factory function — if the function signature accepts a `config`
parameter, Aegra classifies it as a per-request factory and calls it only for real
execution runs, never for schema introspection. The flag is therefore unnecessary.

Without the flag being set, `graph_loaded_for_execution()` always returned `False`,
so the agent always returned the no-op skeleton instead of the real agent. The run
completed with status `success` after a single Anthropic API call but took no action.

**Fix:** Changed the condition to `if thread_id is None` only, dropping the
`graph_loaded_for_execution` check. The `thread_id is None` case still protects
against any edge-case call without a real run context.

## Other Issues Found

### Secrets Manager secret had no AWSCURRENT version

After `terraform apply` recreated the infrastructure today, the `open-swe/env` secret
was recreated empty. ECS cannot pull secrets without an `AWSCURRENT` version. The secret
existed but had no value, producing:

```
ResourceNotFoundException: Secrets Manager can't find the specified secret value for staging label: AWSCURRENT
```

**Fix:** Populated the secret via the AWS Secrets Manager web UI. Also discovered that
`TOKEN_ENCRYPTION_KEY` (an Aegra-specific secret for encrypting OAuth tokens at rest,
generated with `openssl rand -base64 32`) needed to be added.

### Missing Slack keys caused secret fetch failure

The task definition references Slack secret keys (`SLACK_BOT_TOKEN`, etc.) that were
not in the `open-swe/env` secret. ECS fails to start the container if any referenced
secret key is missing.

**Fix:** Added the Slack keys (with empty values since Slack is not in use) and
`LINEAR_API_KEY` to the secret.
