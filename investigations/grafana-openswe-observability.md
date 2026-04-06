# Investigation: Grafana Observability for Open SWE

**Date:** 2026-04-06
**Status:** Research, not yet implemented

## Goal

Use Wendy's existing self-hosted Grafana instance to monitor Open SWE agent runs. Two distinct needs:

1. **Live operations dashboard** -- see active agent threads, current step, tool calls, status (the "Stripe Minions" view)
2. **Tracing/observability** -- LLM call latency, token usage, tool execution times, error rates via OpenTelemetry

## 1. Live Operations Dashboard (Aegra API Polling)

Aegra exposes a REST API for thread and run state. A Grafana dashboard can poll these endpoints using the Infinity plugin (JSON API data source).

### Endpoints

- `GET /threads` -- list all threads with status
- `GET /threads/{id}` -- thread details including metadata (repo, sandbox_id)
- `GET /threads/{id}/runs` -- list runs for a thread with status (queued, running, success, error)
- `GET /threads/{id}/state` -- current state including message history

### Grafana Setup

1. Install the [Infinity data source plugin](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/) (supports JSON API polling)
2. Add a data source pointing at `http://localhost:2026` (local) or the Aegra ALB URL (production)
3. Build panels:

**Active Threads Table:**
- Source: `GET /threads?status=busy` (or filter in Grafana)
- Columns: thread_id, repo, status, created_at, last message timestamp
- Auto-refresh: 10s

**Run Status Timeline:**
- Source: `GET /threads/{id}/runs` for each active thread
- Visualization: state timeline showing queued -> running -> success/error
- Color: green=success, red=error, yellow=running

**Current Agent Step:**
- Source: `GET /threads/{id}/state`
- Parse the last message in the state to show what the agent is currently doing
- Show tool calls in progress

**Sandbox Status:**
- Source: thread metadata contains `sandbox_id`
- Could also poll `docker ps` via a Telegraf/node_exporter sidecar for container resource usage

### Limitations

- Aegra's `/threads` endpoint may not support filtering by status (need to verify)
- No WebSocket/SSE support in Infinity plugin -- polling only, so there's a delay
- Thread state can be large (full message history) -- may need to parse only the last few messages
- The Infinity plugin handles JSON well but complex nested structures may need JSONPath or transformations

## 2. OpenTelemetry Tracing (LLM + Tool Calls)

### Architecture

```
Aegra (Python app)
    |
    | (OTLP/gRPC or OTLP/HTTP)
    |
OTEL Collector (optional, or direct)
    |
    +-- Grafana Tempo (traces)
    +-- Grafana Loki (logs, optional)
    +-- Prometheus (metrics, optional)
    |
Grafana
    +-- Tempo data source (trace viewer)
    +-- Loki data source (correlated logs)
```

### Instrumenting Aegra/LangChain

The `openllmetry` project by Traceloop provides auto-instrumentation for LangChain:

```bash
pip install opentelemetry-sdk \
    opentelemetry-exporter-otlp \
    opentelemetry-instrumentation-langchain
```

Initialize in the app (e.g., add to `agent/server.py` or a startup hook):

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)

from opentelemetry.instrumentation.langchain import LangchainInstrumentor
LangchainInstrumentor().instrument()
```

This auto-creates spans for:
- LLM calls (model, tokens in/out, latency)
- Tool invocations (name, args, result)
- Chain/graph execution (node transitions)
- Retrieval operations

### What the Traces Show

Each agent run becomes a trace with child spans:
```
agent_run (thread_id, repo)
  ├── llm_call (claude-opus-4-6, 1200 tokens in, 450 out, 3.2s)
  ├── tool_call (bash, "ls -la", 0.5s)
  ├── llm_call (claude-opus-4-6, 2100 tokens in, 800 out, 5.1s)
  ├── tool_call (bash, "git diff", 0.3s)
  └── tool_call (commit_and_open_pr, "feat: ...", 2.1s)
```

### Running Tempo Locally

If not already running in Wendy's Grafana stack:

```bash
docker run -d --name tempo \
  -p 3200:3200 \
  -p 4317:4317 \
  -p 4318:4318 \
  -v tempo-data:/var/tempo \
  grafana/tempo:latest \
  -config.file=/etc/tempo/tempo.yaml
```

Or add to an existing docker-compose. Tempo needs minimal config for local dev:

```yaml
server:
  http_listen_port: 3200
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
```

Then add Tempo as a data source in Grafana pointing at `http://localhost:3200`.

### Environment Variables for Aegra

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=openswe
OTEL_TRACES_EXPORTER=otlp
```

## 3. Alternative: Langfuse (Open Source)

Langfuse is purpose-built for LLM observability and has a LangChain callback handler. Easier to set up than raw OTEL for LLM-specific metrics, but adds another service to run.

```bash
pip install langfuse
```

```python
from langfuse.callback import CallbackHandler
handler = CallbackHandler(
    public_key="...",
    secret_key="...",
    host="http://localhost:3000"  # self-hosted Langfuse
)
# Pass handler as a callback to LangChain
```

Langfuse provides:
- Trace waterfall view (similar to Tempo but LLM-aware)
- Token cost tracking
- Model comparison
- Prompt management
- Evaluation/scoring

Trade-off: Langfuse gives a better LLM-specific UI out of the box, but it's another service to maintain alongside Grafana. OTEL + Tempo integrates with the existing Grafana stack.

## Recommendation

Start with the **live operations dashboard** (Infinity plugin + Aegra API) since it addresses the immediate need of watching multiple agents. The OTEL tracing is a deeper investment but more valuable long-term for debugging and cost tracking.

Order of implementation:
1. Infinity data source + basic thread/run status dashboard
2. OTEL instrumentation + Tempo for traces
3. Correlate traces with the dashboard (link from thread panel to trace view)

## Open Questions

1. Does Wendy's Grafana stack already include Tempo or an OTEL collector?
2. What version of Grafana is running? (Infinity plugin requires 9.x+)
3. Should the dashboard work for both local and production Aegra, or just local for now?
4. Is the `opentelemetry-instrumentation-langchain` package compatible with the LangChain version in the aegra venv?
5. How to instrument Aegra without modifying the aegra-api package (which is installed from PyPI)? May need a startup script or monkey-patching.
