# Slack WebSockets (Socket Mode) vs Webhooks

## Date: 2026-04-06

## Question

What would it take to switch the OpenSWE Slack integration from webhooks to Slack Socket Mode (WebSockets)?

## Current State: Webhooks (open-swe-aws-devpod-aegra)

### Key Files
- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/webapp.py` — FastAPI webhook endpoint at `POST /webhooks/slack`
- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/utils/slack.py` — Slack API utilities (raw httpx calls)
- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/tools/slack_thread_reply.py` — Thread reply tool

### How It Works
1. Slack sends HTTP POST to `/webhooks/slack`
2. FastAPI verifies HMAC-SHA256 signature (X-Slack-Signature + timestamp)
3. Parses event, validates type (app_mention or message with bot mention)
4. Returns 200 immediately, queues background task
5. Background task adds reaction, fetches thread history, calls agent via LangGraph
6. Agent uses `slack_thread_reply()` to post responses via `chat.postMessage` API

### Infrastructure Requirements
- Public-facing HTTPS endpoint (Slack pushes to your URL)
- Tokens: `SLACK_BOT_TOKEN` (xoxb-*), `SLACK_SIGNING_SECRET`
- Optional: `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME`

### Dependencies
- FastAPI, httpx — no Slack SDK, all raw HTTP calls

## Reference Implementation: Socket Mode (openclaw)

### Key Files
- `/Users/wsmoak/Projects/openclaw/extensions/slack/src/monitor/provider.ts` — Socket Mode connection handler
- `/Users/wsmoak/Projects/openclaw/extensions/slack/src/client.ts` — WebClient wrapper
- `/Users/wsmoak/Projects/openclaw/extensions/slack/src/monitor/reconnect-policy.ts` — Reconnection logic
- `/Users/wsmoak/Projects/openclaw/extensions/slack/src/monitor/events.ts` — Event handlers

### How It Works
1. App starts and initiates a persistent WebSocket connection to Slack (outbound)
2. Slack authenticates via app-level token (xapp-*)
3. Events arrive over the WebSocket — @slack/bolt middleware chain processes them
4. Handler code responds via WebClient methods (chat.postMessage, etc.)
5. Auto-reconnect on disconnect: exponential backoff (2s to 30s, 1.8x factor, 25% jitter, max 12 attempts)

### Infrastructure Requirements
- NO public endpoint needed — connection is outbound only
- Tokens: `SLACK_BOT_TOKEN` (xoxb-*) + `SLACK_APP_TOKEN` (xapp-1-*)
- `SLACK_SIGNING_SECRET` not needed for Socket Mode

### Dependencies
- `@slack/bolt` ^4.6.0 (TypeScript/Node.js)
- `@slack/web-api` ^7.15.0

## Comparison

| Aspect | Webhook (current) | Socket Mode |
|--------|-------------------|-------------|
| Message receipt | Slack pushes HTTP POST | Persistent WebSocket (app connects out) |
| Public endpoint | Required | Not required |
| Auth model | HMAC signature verification per request | App token authenticates WebSocket |
| Tokens needed | Bot token + signing secret | Bot token + app-level token |
| Latency | HTTP round-trip per event | Lower (persistent connection) |
| Scaling | Stateless, easy horizontal scale | Stateful connection per instance |
| Firewall | Inbound from Slack IPs | Outbound HTTPS only |
| Reconnection | N/A (stateless) | Must handle disconnects and reconnect |

## What It Would Take to Switch

### 1. New Slack App Token
- Generate an app-level token (xapp-*) in the Slack app settings under "Basic Information > App-Level Tokens"
- Grant the `connections:write` scope
- Add to Secrets Manager alongside existing tokens

### 2. Slack App Configuration
- Enable Socket Mode in the Slack app settings (Settings > Socket Mode > Enable)
- Note: this changes how events are delivered — the existing webhook URL will stop receiving events

### 3. Code Changes

**Option A: Python with slack_bolt (recommended for minimal disruption)**
- Add `slack_bolt` and `slack_sdk` to dependencies
- Replace the FastAPI webhook endpoint with a Bolt app in Socket Mode
- Remove manual signature verification (Bolt handles it)
- Keep the existing httpx-based response code, or migrate to slack_sdk WebClient

Rough structure:
```python
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=SLACK_BOT_TOKEN)

@app.event("app_mention")
def handle_mention(event, say):
    # existing logic: fetch thread, call agent, reply
    ...

handler = SocketModeHandler(app, SLACK_APP_TOKEN)
handler.start()
```

**Option B: Keep raw websocket management (no SDK)**
- Use `websockets` or `aiohttp` to maintain a WebSocket connection
- Implement reconnection logic manually
- More work, less benefit — not recommended

### 4. Infrastructure Changes
- The `/webhooks/slack` endpoint can be removed from the FastAPI app
- No longer need to expose the webhook path publicly or configure Slack to hit it
- The ECS task still needs outbound internet access (already the case)
- May simplify networking if the webhook endpoint was the only reason for a public-facing load balancer path

### 5. Operational Considerations
- **Single connection**: Socket Mode maintains one WebSocket connection per app instance. If running multiple ECS tasks, only one should handle the Slack connection, or use a coordination mechanism.
- **Reconnection**: Must handle disconnects gracefully. The `slack_bolt` SocketModeHandler has built-in reconnection. The openclaw implementation adds custom exponential backoff (2s-30s, 12 max attempts).
- **Startup dependency**: The WebSocket must connect before the bot can receive messages. If the connection fails on startup, the bot is deaf until reconnected.
- **No retry on delivery**: With webhooks, Slack retries failed deliveries (3 attempts). With Socket Mode, if you don't acknowledge an event, it's gone.

### 6. Migration Path
1. Generate app-level token, add to Secrets Manager
2. Add `slack_bolt` to pyproject.toml dependencies
3. Write a SocketMode handler alongside the existing webhook (both can coexist briefly)
4. Enable Socket Mode in Slack app settings
5. Test, then remove the webhook endpoint code

## Recommendation

Socket Mode is a good fit for this use case. The main benefits:
- Eliminates the need for a public webhook URL (simpler networking)
- Lower latency for event delivery
- `slack_bolt` for Python is well-maintained and handles reconnection

The main risk is the single-connection constraint — if running multiple replicas, only one should handle the Slack WebSocket. Since OpenSWE currently runs as a single ECS task, this is not an issue today but would need addressing if horizontally scaled later.

The Python `slack_bolt` library makes this straightforward. The core change is ~50 lines of new code replacing the webhook handler, plus dependency additions.
