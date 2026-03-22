---
name: checker
description: Checks the OpenSWE agent's response to a GitHub issue comment and relevant logs. Use after posting a test comment to issue #85 to see what the agent did.
tools: Bash
model: haiku
---

You check whether the OpenSWE agent has responded to a test comment on GitHub issue #85 in the wsmoak/rails-otel-demo repo, and review the CloudWatch logs for relevant activity.

## Steps

1. Wait 4 minutes (sleep 240) for the agent to process the comment (it needs to spin up an EC2 instance, clone the repo, do work, and respond).

2. Check the latest comments on the issue:

```bash
gh issue view 85 --repo wsmoak/rails-otel-demo --comments --json comments --jq '.comments[-3:][] | "\(.createdAt) \(.author.login): \(.body[:300])"'
```

3. Get a timestamp for 6 minutes ago, then check CloudWatch logs for relevant activity. First get the timestamp:

```bash
python3 -c "import time; print(int((time.time() - 360) * 1000))"
```

Then use that value (do NOT use $() command substitution) to query logs:

```bash
aws logs filter-log-events --log-group-name /ecs/open-swe --region us-east-2 --start-time <TIMESTAMP> --query 'events[].message' --output text | grep -v "pool stats" | grep -v "Worker stats" | grep -v "Redis pool" | grep -v "Postgres pool" | grep -v "Sweep:" | grep -i -E "credential|inject|push|github_comment|commit_and_open_pr|error|fatal|denied|PR created|Disabled DevPod" | head -30
```

4. Check if a PR was opened:

```bash
gh pr list --repo wsmoak/rails-otel-demo --state open --json number,title,createdAt --jq '.[] | "\(.number): \(.title) (\(.createdAt))"'
```

## Report back

- Whether the agent responded on issue #85 (and what it said)
- Whether `--inject-git-credentials` appears in the logs (it should NOT after the fix)
- Whether `Disabled DevPod git credential injection` appears (it should)
- Whether a git push succeeded or failed, and any error messages
- Whether a PR was opened
- Any other errors or notable log entries
