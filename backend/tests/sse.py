"""Parses a Server-Sent Events body into (event, id, data) tuples."""

import json
from typing import Any


def parse_sse(body: str) -> list[tuple[str | None, str | None, Any]]:
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event = event_id = None
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                data.append(line[6:])
        if data:
            events.append((event, event_id, json.loads("\n".join(data))))
    return events
