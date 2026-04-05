# Aegra Migration Handoff: Factory Architecture Mismatch

**Date:** 2026-04-04
**Status:** Blocked — needs Aegra documentation review and architectural decision

---

Update after secondary investigation: The flow is clear:

1. thread_id comes from the URL path parameter (line 345-346)
2. execute_run_async(run_id, thread_id, ...) passes it
3. create_run_config() adds it to cfg["configurable"] via setdefault("thread_id", thread_id) (line 729)
4. get_graph(config=run_config) passes the config (with thread_id) to the factory

The handoff document's (below) core premise appears to be wrong. Aegra DOES pass thread_id in the config before calling the factory.

---

## Where We Are

We got OpenSWE running on Aegra (ECS, healthy, webhooks received, runs created) but
the agent takes no action. The root cause is an architectural mismatch between how
OpenSWE's `get_agent()` factory was designed for LangGraph Platform vs. how Aegra
actually calls graph factories.

---

## What Works

- Docker image builds and runs on ECS
- `aegra.json` correctly points to `./agent/server.py:get_agent`
- `agent/server.py` imports work (fixed relative → absolute)
- GitHub webhooks are received and processed by `webapp.py`
- Runs are created, Aegra picks them up, `get_agent` is called
- Anthropic API calls succeed
- Run completes with `status=success`

---

## The Core Problem

### How OpenSWE's get_agent was designed (LangGraph Platform)

`get_agent(config: RunnableConfig)` is a **per-request factory**. It was designed to:

1. Receive the full run config including `thread_id`
2. Look up or create a DevPod sandbox for that thread
3. Return a compiled LangGraph agent wired to that sandbox

LangGraph Platform called this factory at execution time with the fully-merged config
(including `thread_id`, checkpoint data, etc.).

It also called it at startup for schema introspection — which is why the
`__is_for_execution__` guard existed (to return a no-op agent during introspection).

### How Aegra actually calls graph factories

Aegra classifies factories by their parameter signature (see
`aegra/libs/aegra-api/src/aegra_api/services/graph_factory.py`, `_classify_factory`):

- **0-arg**: called once at load time, result is cached
- **1-arg (config)**: called per-request with the **run's request config**
- **1-arg (ServerRuntime)**: called per-request with the server runtime
- **2-arg (config + ServerRuntime)**: called per-request with both

The key issue: Aegra calls the factory with `run_config = config or {"configurable": {}}`,
where `config` is the request body's config field from `webapp.py`. The `thread_id` is
**not yet in this config** — it gets injected later by `create_run_config()`:

```python
# langgraph_service.py line 729
cfg["configurable"].setdefault("thread_id", thread_id)
```

This injection happens **after** the factory is invoked to get the graph. So `get_agent`
receives the config without `thread_id`, the check `if thread_id is None` triggers, and
a no-op agent is returned.

### Evidence

Log line every time a run fires:
```
No thread_id, returning agent without sandbox
```

The `thread_id` is visible in all `webapp.py` logs (thread checks, run creation) but
never makes it into the factory's config.

---

## What Needs Investigation

### Option 1: Use Aegra's ServerRuntime

Aegra has a `ServerRuntime` object passed to 2-arg factories. It may provide access to
the thread context at execution time. The `get_agent` signature could become:

```python
async def get_agent(config: RunnableConfig, runtime: ServerRuntime) -> Pregel:
```

Check what `ServerRuntime` provides — does it include `thread_id`? See:
- `aegra/libs/aegra-api/src/aegra_api/services/graph_factory.py`
- `aegra/libs/aegra-api/src/aegra_api/models/runs.py`

### Option 2: Move sandbox setup into the graph

Instead of setting up the sandbox in the factory, set it up in the graph's first node.
The factory just returns a compiled graph. The first node calls `get_config()` (from
`langgraph.config`) to get the current `thread_id` and sets up the sandbox.

This is probably the most "correct" LangGraph pattern — the factory defines the graph
structure, and the graph nodes do the work.

The `SANDBOX_BACKENDS` dict in `server.py` already tracks sandbox state per thread_id,
so this dict would just need to be populated from within the graph rather than the factory.

### Option 3: Pass thread_id via webapp.py configurable

Since `webapp.py` already knows the `thread_id` when it creates the run (it's passed as
the URL path parameter to `POST /threads/{thread_id}/runs`), it could add it to the
configurable:

```python
configurable: dict[str, Any] = {
    "thread_id": thread_id,   # add this
    "__is_for_execution__": True,
    "source": "github",
    ...
}
```

This might work if Aegra passes the configurable to the factory before injecting its own
`thread_id`. Needs testing.

### Option 4: Read Aegra documentation / examples

Aegra's README and `INSTALLATION.md` describe how to set up agents. The examples in
`/Users/wsmoak/Projects/aegra/examples/` show simple graphs without sandbox setup.
There may be documented patterns for stateful per-thread resources.

Key files to read:
- `/Users/wsmoak/Projects/aegra/INSTALLATION.md`
- `/Users/wsmoak/Projects/aegra/examples/factory/graph.py` — uses a factory pattern
- `/Users/wsmoak/Projects/aegra/libs/aegra-api/src/aegra_api/services/graph_factory.py`

---

## Current State of the Fork

Branch: `open-swe-aws-devpod-aegra` in `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra`

Recent commits:
```
44dada35 Remove LangGraph Platform __is_for_execution__ guard, use thread_id presence instead
537aad95 Add __is_for_execution__ to all run configurable dicts so agent uses sandbox  (probably revert)
cddf2d48 Fix relative imports in server.py to absolute imports for Aegra compatibility
11bb2a19 Fix aegra.json graph path format for Aegra (file path not module path)
```

The `537aad95` commit (adding `__is_for_execution__` to `webapp.py` configurable dicts)
is probably worth reverting since it doesn't solve anything — the real fix needs to be
one of the options above.

---

## Key Files

| File | Purpose |
|------|---------|
| `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/aegra.json` | Aegra config — graph and webapp paths |
| `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/server.py` | `get_agent()` factory — WHERE THE PROBLEM IS |
| `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/webapp.py` | Webhook handlers, run creation |
| `/Users/wsmoak/Projects/aegra/libs/aegra-api/src/aegra_api/services/graph_factory.py` | Aegra factory classification logic |
| `/Users/wsmoak/Projects/aegra/libs/aegra-api/src/aegra_api/services/langgraph_service.py` | Aegra run execution, config injection |
| `/Users/wsmoak/Projects/aegra/examples/factory/graph.py` | Aegra factory example to study |

---

## Infrastructure

- ECS cluster: `open-swe` in `us-east-2`
- ECR image: `255104623693.dkr.ecr.us-east-2.amazonaws.com/open-swe:latest`
- Service URL: `https://openswe.wendysmoak.com`
- Health check: `https://openswe.wendysmoak.com/health`
- CloudWatch logs: `/ecs/open-swe`
- RDS endpoint: `open-swe-aegra.c38gy2owmo0a.us-east-2.rds.amazonaws.com:5432`
- Secrets: `open-swe/env` in Secrets Manager (ARN: `arn:aws:secretsmanager:us-east-2:255104623693:secret:open-swe/env-KbYM21`)
- Test issue for triggering runs: `https://github.com/wsmoak/rails-otel-demo/issues/94`

---

## How to Test

Post a comment to the test issue tagging `@openswe`:

```
gh issue comment 94 --repo wsmoak/rails-otel-demo --body "@openswe please open a PR to add a line to the end of the README.md file that says 'Updated by OpenSWE on 2026-04-04'."
```

Watch CloudWatch logs for `No thread_id` — once that line is gone and you see sandbox
creation / DevPod activity, the agent is working.

## Deploy Cycle

```
builder → pusher → deployer → watcher
```

All are subagents in `/Users/wsmoak/Projects/open-swe-aws-devpod-project/.claude/agents/`.
Use them — do not run the commands in Bash directly (too much output).
