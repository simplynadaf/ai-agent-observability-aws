"""Decisive double-count check.

The first probe's heuristic was inconclusive because the sub-agents were trivial.
This probe answers the question mechanically instead of by ratio:

Strands agents-as-tools wraps each sub-agent as a @tool. When the supervisor calls
that tool, a BRAND NEW Agent() with its OWN event loop runs inside; the supervisor
only receives the tool's string return value. Token usage is accumulated on each
Agent's own EventLoopMetrics. So the supervisor's accumulated_usage should reflect
ONLY the supervisor's own model invocations, NOT the sub-agents'.

We prove it by summing the supervisor's per-cycle usage from its own event-loop
metrics and comparing to accumulated_usage. If they match and are independent of the
sub-agent totals, usage is EXCLUSIVE (attribute each level to its own span, no delta).
"""
from __future__ import annotations

import json

from strands import Agent, tool
from strands.models import BedrockModel

REGION = "us-east-1"
NOVA = "amazon.nova-pro-v1:0"


def _model() -> BedrockModel:
    return BedrockModel(model_id=NOVA, region_name=REGION, temperature=0.2, max_tokens=256)


def _usage(result) -> dict:
    u = result.metrics.accumulated_usage
    try:
        return dict(u)
    except (TypeError, ValueError):
        return {k: getattr(u, k) for k in ("inputTokens", "outputTokens", "totalTokens") if hasattr(u, k)}


CALLS = {"heavy": 0}


@tool
def heavy(question: str) -> str:
    """A sub-agent that deliberately produces a LOT of output tokens."""
    CALLS["heavy"] += 1
    a = Agent(
        model=_model(),
        system_prompt="Write a detailed 150-word paragraph. Be verbose.",
    )
    result = a(question)
    u = _usage(result)
    print(f"[sub-agent heavy] usage = {json.dumps(u)}")
    return result.message["content"][0]["text"]


def main() -> None:
    # Run 1: supervisor that does NOT call the sub-agent (answers directly).
    sup1 = Agent(
        model=_model(),
        tools=[heavy],
        system_prompt="Answer in ONE short sentence. Do not call any tool.",
    )
    r1 = sup1("Say hello.")
    u1 = _usage(r1)
    print("NO-TOOL supervisor usage:", json.dumps(u1), "| heavy calls:", CALLS["heavy"])

    # Run 2: supervisor that MUST call the heavy sub-agent (big sub-agent output).
    CALLS["heavy"] = 0
    sup2 = Agent(
        model=_model(),
        tools=[heavy],
        system_prompt=(
            "You MUST call the heavy tool to answer, then reply with ONE short sentence."
        ),
    )
    r2 = sup2("Explain what cloud computing is (use the heavy tool).")
    u2 = _usage(r2)
    print("WITH-TOOL supervisor usage:", json.dumps(u2), "| heavy calls:", CALLS["heavy"])

    print("\n=== INTERPRETATION ===")
    print(
        "If the supervisor called heavy (WITH-TOOL heavy calls >= 1) but its outputTokens\n"
        "did NOT jump by the ~150-word sub-agent output, then supervisor usage is EXCLUSIVE\n"
        "(it does NOT include sub-agent tokens). Compare WITH-TOOL vs NO-TOOL supervisor\n"
        "outputTokens against the printed [sub-agent heavy] usage above."
    )
    print("RUN-DONE")


if __name__ == "__main__":
    main()
