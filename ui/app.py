"""Live control-panel backend for the AWS Account Investigator crew.

A tiny FastAPI app that serves a single-page UI and streams the crew's per-agent
lifecycle over Server-Sent Events (SSE). Two modes:

  * LIVE (default): registers a callback on crew.py and runs the REAL crew in a worker
    thread. Every number the UI shows (tokens, $, latency) is computed by that run -
    real Amazon Nova Pro calls on Bedrock, real read-only boto3 calls. ~$0.011/run.

  * REPLAY (?replay=1): reconstructs a deterministic timeline from a committed sample
    trace (replay/sample_run.jsonl + replay/sample_report.md). Zero Bedrock calls, zero
    cost, identical every time - safe for rehearsals and retakes.

The crew is imported unchanged; the only integration point is crew.set_event_callback,
which is a no-op when not set (so `python -m src.crew` in the terminal is byte-for-byte the
same $0/local path). This backend runs locally (localhost:8000) on the machine you're
already using; it is not a hosted service and exposes nothing to the internet.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

# Import the real crew from the parent directory (this file lives in ui/).
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# The three specialists, in the visual order shown on the panel.
SPECIALISTS = [
    {"key": "cost_analyst", "agent_id": "cost-analyst", "agent_name": "Cost Analyst", "role": "cost"},
    {"key": "health_ops", "agent_id": "health-ops", "agent_name": "Health & Ops", "role": "health"},
    {"key": "security_ops", "agent_id": "security-auditor", "agent_name": "Security Auditor", "role": "security"},
]

REPLAY_TRACE = HERE / "replay" / "sample_run.jsonl"
REPLAY_REPORT = HERE / "replay" / "sample_report.md"

app = FastAPI(title="AWS Account Investigator - Live Panel")


def _sse(event: str, data: dict) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --------------------------------------------------------------------------- LIVE mode
def _live_events(query: str):
    """Run the REAL crew in a worker thread and yield its lifecycle events as SSE.

    crew.set_event_callback pushes events onto a thread-safe queue; this generator
    drains the queue and formats SSE frames until the run completes.
    """
    from src import crew  # imported here so import cost is paid on first request, not at boot

    q: "queue.Queue[tuple[str, dict]]" = queue.Queue()

    def cb(kind: str, data: dict) -> None:
        q.put((kind, data))

    crew.set_event_callback(cb)

    error_box: dict = {}

    def worker():
        try:
            crew.run(query)
        except Exception as exc:  # surface a real failure to the UI honestly
            error_box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            q.put(("__done__", error_box))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    yield _sse("mode", {"mode": "live", "query": query})
    while True:
        kind, data = q.get()
        if kind == "__done__":
            if data.get("error"):
                yield _sse("run_error", {"error": data["error"]})
            yield _sse("run_done", {})
            break
        yield _sse(kind, data)

    crew.set_event_callback(None)  # unregister so the terminal path stays pristine


# ------------------------------------------------------------------------- REPLAY mode
def _load_replay():
    """Reconstruct a per-agent timeline from the committed sample trace.

    Returns (agents_in_start_order, report_text). Each agent dict carries the REAL
    recorded numbers (tokens, cost, latency, stop reason) so replay shows true figures,
    just deterministically and for free.
    """
    spans = []
    for line in REPLAY_TRACE.read_text().splitlines():
        if not line.strip():
            continue
        for ss in json.loads(line).get("scopeSpans", []):
            spans.extend(ss.get("spans", []))

    # Only the agent-level LLM spans (one per agent). Map trace name -> our key.
    name_to_key = {
        "cost_analyst": "cost_analyst",
        "health_ops": "health_ops",
        "security_ops": "security_ops",
        "investigation_run": "supervisor",
    }
    agents = []
    for s in spans:
        a = s.get("attributes", {})
        if a.get("span.type") != "LLM":
            continue
        key = name_to_key.get(s["name"])
        if key is None:
            continue
        agents.append({
            "key": key,
            "agent_id": a.get("agent.id"),
            "agent_name": a.get("agent.name"),
            "input_tokens": a.get("llm.usage.prompt_tokens"),
            "output_tokens": a.get("llm.usage.completion_tokens"),
            "total_tokens": a.get("llm.usage.total_tokens"),
            "cost_usd": a.get("llm.cost.usd"),
            "latency_ms": a.get("llm.latency_ms"),
            "cycles": a.get("agent.cycle_count"),
            "stop_reason": a.get("llm.finish_reason"),
            "start": s["startTimeUnixNano"],
        })
    agents.sort(key=lambda x: x["start"])
    report = REPLAY_REPORT.read_text() if REPLAY_REPORT.exists() else "(no sample report)"
    return agents, report


def _replay_events(query: str):
    """Animate the saved run deterministically. Same event shapes as live, no cost."""
    agents, report = _load_replay()
    session_id = "replay-" + str(int(time.time()))
    supervisor = next((a for a in agents if a["key"] == "supervisor"), None)
    specialists = [a for a in agents if a["key"] != "supervisor"]

    yield _sse("mode", {"mode": "replay", "query": query})
    yield _sse("run_started", {
        "session_id": session_id, "query": query,
        "supervisor_id": (supervisor or {}).get("agent_id", "aws-investigator"),
        "supervisor_name": (supervisor or {}).get("agent_name", "AWS Account Investigator"),
    })
    time.sleep(0.6)

    # Fan out: start all three specialists, then finish them one by one with real
    # numbers, pacing by their recorded latency (scaled to feel live but bounded).
    for sp in specialists:
        yield _sse("agent_started", {"key": sp["key"], "agent_id": sp["agent_id"],
                                     "agent_name": sp["agent_name"]})
        time.sleep(0.25)

    for sp in specialists:
        # Scale recorded latency into a watchable pace (cap so replay stays snappy).
        dwell = min(max((sp["latency_ms"] or 1500) / 1000.0, 1.0), 3.5)
        time.sleep(dwell)
        yield _sse("agent_finished", {
            "key": sp["key"], "agent_id": sp["agent_id"], "agent_name": sp["agent_name"],
            "input_tokens": sp["input_tokens"], "output_tokens": sp["output_tokens"],
            "total_tokens": sp["total_tokens"], "cost_usd": sp["cost_usd"],
            "latency_ms": sp["latency_ms"], "cycles": sp["cycles"],
            "stop_reason": sp["stop_reason"], "tools_called": [],
        })

    time.sleep(0.5)
    if supervisor:
        yield _sse("supervisor_finished", {
            "session_id": session_id, "agent_id": supervisor["agent_id"],
            "agent_name": supervisor["agent_name"],
            "input_tokens": supervisor["input_tokens"], "output_tokens": supervisor["output_tokens"],
            "total_tokens": supervisor["total_tokens"], "cost_usd": supervisor["cost_usd"],
            "latency_ms": supervisor["latency_ms"], "cycles": supervisor["cycles"],
            "delegated_to": [s["key"] for s in specialists],
        })
    time.sleep(0.4)
    yield _sse("final_report", {"session_id": session_id, "report": report})
    yield _sse("run_done", {})


# ----------------------------------------------------------------------------- routes
DEFAULT_QUERY = ("What's running in my account, any security risks, and where is my "
                 "month-to-date spend going?")


@app.get("/stream")
def stream(q: str = DEFAULT_QUERY, replay: int = 0):
    gen = _replay_events(q) if replay else _live_events(q)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
