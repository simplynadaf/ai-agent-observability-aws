"""Multi-agent cost-attribution probe.

Purpose: answer the one question that decides how the cost bridge works, BEFORE we
build the full crew and before any recording:

    Does a supervisor agent's `result.metrics.accumulated_usage` ALREADY include the
    tokens spent by sub-agents it called (via agents-as-tools)?

If YES, attributing the supervisor's full usage to the supervisor span AND each
sub-agent's usage to its own span double-counts the crew total. In that case the
supervisor span should only carry the DELTA (supervisor_total - sum(sub_agents)).

This probe runs a tiny supervisor with two trivial sub-agents (no AWS calls needed
to answer the token question) and prints usage at every level so the rule is decided
from real numbers, not a guess. It also prints the exact key case of the usage dict
(inputTokens vs input_tokens).
"""
from __future__ import annotations

import json

from strands import Agent, tool
from strands.models import BedrockModel

REGION = "us-east-1"
NOVA = "amazon.nova-pro-v1:0"


def _model() -> BedrockModel:
    return BedrockModel(
        model_id=NOVA, region_name=REGION, temperature=0.2, max_tokens=256
    )


def _usage(result) -> dict:
    """Return accumulated_usage as a plain dict, whatever its underlying type."""
    u = result.metrics.accumulated_usage
    try:
        return dict(u)
    except (TypeError, ValueError):
        # Fall back to attribute access if it is an object, not a mapping.
        return {
            k: getattr(u, k)
            for k in ("inputTokens", "outputTokens", "totalTokens")
            if hasattr(u, k)
        }


# Capture each sub-agent's usage as a side effect when the supervisor calls it.
SUBAGENT_USAGE: dict[str, dict] = {}


@tool
def adder(question: str) -> str:
    """Answer a simple arithmetic question. Delegated sub-agent."""
    a = Agent(
        model=_model(),
        system_prompt="You are a calculator. Answer with just the number.",
    )
    result = a(question)
    SUBAGENT_USAGE["adder"] = _usage(result)
    return result.message["content"][0]["text"]


@tool
def speller(question: str) -> str:
    """Spell a word out letter by letter. Delegated sub-agent."""
    a = Agent(
        model=_model(),
        system_prompt="You spell words letter by letter, e.g. C-A-T.",
    )
    result = a(question)
    SUBAGENT_USAGE["speller"] = _usage(result)
    return result.message["content"][0]["text"]


def main() -> None:
    supervisor = Agent(
        model=_model(),
        tools=[adder, speller],
        system_prompt=(
            "You are a supervisor. For math questions call adder; to spell a word "
            "call speller. Always call the right tool, then give a one-line answer."
        ),
    )
    result = supervisor("What is 17 plus 25, and how do you spell the word cloud?")

    sup_usage = _usage(result)
    sub_total = {
        "inputTokens": sum(u.get("inputTokens", 0) for u in SUBAGENT_USAGE.values()),
        "outputTokens": sum(u.get("outputTokens", 0) for u in SUBAGENT_USAGE.values()),
        "totalTokens": sum(u.get("totalTokens", 0) for u in SUBAGENT_USAGE.values()),
    }

    print("=== SUPERVISOR accumulated_usage ===")
    print(json.dumps(sup_usage, indent=2))
    print("\n=== PER SUB-AGENT accumulated_usage ===")
    print(json.dumps(SUBAGENT_USAGE, indent=2))
    print("\n=== SUM of sub-agents ===")
    print(json.dumps(sub_total, indent=2))

    sup_total = sup_usage.get("totalTokens", 0)
    delta = sup_total - sub_total["totalTokens"]
    print("\n=== VERDICT ===")
    print(f"supervisor.totalTokens          = {sup_total}")
    print(f"sum(sub-agents).totalTokens     = {sub_total['totalTokens']}")
    print(f"supervisor - sum(sub-agents)    = {delta}")
    if sub_total["totalTokens"] and sup_total >= sub_total["totalTokens"] * 1.5:
        print(
            "LIKELY INCLUSIVE: supervisor usage appears to INCLUDE sub-agent tokens. "
            "Rule -> attribute only the DELTA to the supervisor span."
        )
    else:
        print(
            "LIKELY EXCLUSIVE: supervisor usage looks like its OWN calls only. "
            "Rule -> attribute each level's usage to its own span (no delta needed)."
        )
    print("RUN-DONE")


if __name__ == "__main__":
    main()
