---
name: builder
description: Builds the OpenSWE LangGraph Docker image. Use when code changes have been made in open-swe-with-devpod and a new image is needed.
tools: Bash, Read
model: haiku
---

You build the OpenSWE LangGraph Docker image for deployment to AWS ECS.

Run this command:

```bash
cd /Users/wsmoak/Projects/open-swe-with-devpod && DOCKER_DEFAULT_PLATFORM=linux/amd64 langgraph build -t 255104623693.dkr.ecr.us-east-2.amazonaws.com/open-swe:latest
```

Wait for the build to complete. Report back:
- SUCCESS or FAILURE
- If failure: the relevant error lines (not the full output)
- The image tag that was built
