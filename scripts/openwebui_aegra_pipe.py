"""
title: Open SWE
author: wsmoak
version: 0.3.0
description: Pipeline that connects Open WebUI to a local Aegra/OpenSWE server.
"""

import json
import os
import requests
from typing import Generator, Iterator, List, Union
from pydantic import BaseModel, Field


class Pipeline:
    class Valves(BaseModel):
        AEGRA_URL: str = Field(
            default="http://host.docker.internal:2026",
            description="URL of the Aegra server",
        )
        DEFAULT_REPO_OWNER: str = Field(
            default="wsmoak",
            description="Default GitHub repo owner",
        )
        DEFAULT_REPO_NAME: str = Field(
            default="rails-otel-demo",
            description="Default GitHub repo name",
        )

    def __init__(self):
        self.name = "Open SWE"
        self.valves = self.Valves(
            **{k: os.getenv(k, v.default) for k, v in self.Valves.model_fields.items()}
        )
        self._thread_map: dict[str, str] = {}

    async def on_startup(self):
        print(f"on_startup: {__name__}")

    async def on_shutdown(self):
        print(f"on_shutdown: {__name__}")

    def _get_or_create_thread(self, chat_id: str) -> str:
        """Map an Open WebUI chat ID to an Aegra thread ID."""
        if chat_id in self._thread_map:
            return self._thread_map[chat_id]

        resp = requests.post(f"{self.valves.AEGRA_URL}/threads", json={}, timeout=30)
        resp.raise_for_status()
        thread_id = resp.json()["thread_id"]
        self._thread_map[chat_id] = thread_id
        return thread_id

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: List[dict],
        body: dict,
    ) -> Union[str, Generator, Iterator]:

        chat_id = body.get("chat_id", "default")

        try:
            thread_id = self._get_or_create_thread(chat_id)
        except Exception as e:
            return f"Failed to create Aegra thread: {e}"

        run_payload = {
            "assistant_id": "agent",
            "input": {
                "messages": [{"role": "user", "content": user_message}]
            },
            "config": {
                "configurable": {
                    "repo": {
                        "owner": self.valves.DEFAULT_REPO_OWNER,
                        "name": self.valves.DEFAULT_REPO_NAME,
                    }
                }
            },
            "stream_mode": "values",
        }

        try:
            response = requests.post(
                f"{self.valves.AEGRA_URL}/threads/{thread_id}/runs/stream",
                json=run_payload,
                stream=True,
                timeout=300,
            )
            response.raise_for_status()
        except Exception as e:
            return f"Error connecting to Aegra: {e}"

        def generate():
            # Track message IDs we've already yielded to avoid duplication.
            # Each values event has the full message list; we only yield new ones.
            seen_msg_ids = set()
            event_type = None

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:") and event_type == "values":
                    try:
                        data = json.loads(line[5:].strip())
                        for msg in data.get("messages", []):
                            msg_id = msg.get("id")
                            if not msg_id or msg_id in seen_msg_ids:
                                continue
                            seen_msg_ids.add(msg_id)

                            msg_type = msg.get("type", "")
                            content = msg.get("content", "")

                            if msg_type == "ai" and content:
                                if isinstance(content, list):
                                    text_parts = [
                                        c.get("text", "") if isinstance(c, dict) else str(c)
                                        for c in content
                                    ]
                                    content = "\n".join(p for p in text_parts if p)
                                if content:
                                    yield content + "\n\n"
                            elif msg_type == "ai" and msg.get("tool_calls"):
                                for tc in msg["tool_calls"]:
                                    name = tc.get("name", "unknown")
                                    args = tc.get("args", {})
                                    if isinstance(args, dict):
                                        preview = args.get("command", args.get("query", str(args)[:100]))
                                    else:
                                        preview = str(args)[:100]
                                    yield f"**Tool: {name}** `{preview}`\n\n"
                            elif msg_type == "tool":
                                tool_content = content[:500] if content else ""
                                if tool_content:
                                    yield f"```\n{tool_content}\n```\n\n"
                    except json.JSONDecodeError:
                        pass

        return generate()
