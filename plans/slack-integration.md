# Plan: Slack Integration for OpenSWE

## Context

OpenSWE supports Slack as a trigger source alongside GitHub and Linear. The code is already in place (`agent/webapp.py`, `agent/utils/slack.py`, `agent/tools/slack_thread_reply.py`). The Terraform config in `secrets.tf` defines the 6 Slack env vars and injects them into ECS. The secrets exist in AWS Secrets Manager with empty values. This plan covers creating the Slack App and populating those secrets.

## Prerequisites

- A Slack workspace where Wendy has admin/app-creation permissions
- The webhook URL is `https://openswe.wendysmoak.com/webhooks/slack` (per RUNBOOK.md)

## Steps

### 1. Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) -> **Create New App** -> **From a manifest**
2. Select the target workspace
3. Use this manifest (JSON), replacing the request URL:

```json
{
    "display_information": {
        "name": "Open SWE",
        "description": "Enables Open SWE to interact with your workspace",
        "background_color": "#000000"
    },
    "features": {
        "app_home": {
            "home_tab_enabled": false,
            "messages_tab_enabled": true,
            "messages_tab_read_only_enabled": false
        },
        "bot_user": {
            "display_name": "Open SWE",
            "always_online": true
        }
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "reactions:write",
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "chat:write",
                "groups:history",
                "groups:read",
                "im:history",
                "im:read",
                "im:write",
                "mpim:history",
                "mpim:read",
                "team:read",
                "users:read",
                "users:read.email"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "request_url": "https://openswe.wendysmoak.com/webhooks/slack",
            "bot_events": [
                "app_mention",
                "message.im",
                "message.mpim"
            ]
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```

Note: The manifest from INSTALLATION.md includes an `oauth_config.redirect_urls` for LangSmith OAuth. We are not using LangSmith hosted OAuth, so that section is omitted. If Slack rejects the manifest without it, we can add the scopes manually instead.

4. Click **Create**
5. **Install to Workspace** when prompted

### 2. Collect Credentials

From the Slack App settings pages, collect:

| Secret Key | Where to Find |
|---|---|
| `SLACK_BOT_TOKEN` | **OAuth & Permissions** -> Bot User OAuth Token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | **Basic Information** -> App Credentials -> Signing Secret |
| `SLACK_BOT_USER_ID` | In Slack, click the bot's profile -> copy the Member ID (starts with `U`) |
| `SLACK_BOT_USERNAME` | The bot's display name, e.g. `open-swe` |

### 3. Default Repo

- `SLACK_REPO_OWNER` = `wsmoak`
- `SLACK_REPO_NAME` = `multi-repo-dev-containers`

This is the multi-repo devcontainer setup that can work across multiple projects. Users can override per-message with `repo:owner/name` syntax if targeting a single repo.

### 4. Populate AWS Secrets Manager

Update the `open-swe/env` secret in AWS Secrets Manager (us-east-2) via the AWS Console. Set values for all 6 keys:

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_BOT_USER_ID`
- `SLACK_BOT_USERNAME`
- `SLACK_REPO_OWNER`
- `SLACK_REPO_NAME`

### 5. Force ECS Redeployment

After updating secrets, force a new deployment so the containers pick up the new values:

```bash
aws ecs update-service --cluster open-swe --service open-swe --force-new-deployment
```

Then watch the deployment to confirm the new task starts successfully.

### 6. Verify Webhook Endpoint

Slack will have already attempted to verify the request URL when the app was created (step 1). If it failed at that time (because the service wasn't running with the signing secret yet), go back to **Event Subscriptions** in the Slack App settings and re-enter/retry the Request URL:

```
https://openswe.wendysmoak.com/webhooks/slack
```

Slack sends a `url_verification` challenge; the webapp handles this at `webapp.py:987`. The GET endpoint also returns a simple status check:

```bash
curl https://openswe.wendysmoak.com/webhooks/slack
# Expected: {"status":"ok","message":"Slack webhook endpoint is active"}
```

### 7. Invite Bot to Channel

In Slack, go to the channel where you want to use OpenSWE and invite the bot:
- Type `/invite @Open SWE` or add it from channel settings

### 8. Test

In the channel, mention the bot:
```
@Open SWE repo:wsmoak/rails-otel-demo add the current timestamp to the end of the README
```

Expected behavior:
1. Bot adds an eyes reaction to the message
2. Bot replies with "Using repository: `wsmoak/rails-otel-demo`"
3. Bot replies with a LangSmith trace link
4. Bot posts updates via `slack_thread_reply` as it works
5. A PR is created in the target repo

## Potential Issues

- **URL verification timing**: Slack verifies the request URL immediately when you set it. If the ECS service doesn't have the signing secret yet, verification will fail. Solution: set up the app without event subscriptions first, populate secrets and redeploy, then add the event subscription URL.
- **Signing secret mismatch**: If webhook requests return 401, double-check the signing secret value in Secrets Manager matches what's shown in Slack App settings.
- **Bot not responding to mentions**: Verify `SLACK_BOT_USER_ID` is correct. Check ECS logs for webhook receipt.

## Files Reference

- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/webapp.py` — webhook handler (lines 987-1082)
- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/utils/slack.py` — Slack API utilities
- `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/tools/slack_thread_reply.py` — agent tool for posting to threads
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/secrets.tf` — secret key definitions
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/ecs.tf` — ECS task definition with secret injection
