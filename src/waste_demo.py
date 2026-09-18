"""Silent-waste demos: a multi-agent run can return a PERFECT answer and still quietly
cost far more than it should. APM says HTTP 200. The trace says otherwise.

This is the unique payoff of the article: not "here is a trace tree" (table stakes),
but "here is the trace tree CATCHING money you would never see in APM" - on the same
read-only AWS crew, reproducible at $0 (local file exporter, real Nova Pro tokens).

Every scenario here is ENGINEERED to trigger deterministically (LLM output is
non-deterministic, so we do not rely on luck). We say so honestly. Each maps to a REAL
Strands EventLoopMetrics signal that our Traccia bridge already stamps on the span:

  Scenario            Real signal                    Span attribute
  ------------------  -----------------------------  -----------------------
  1. Runaway loop     extra event-loop cycles        agent.cycle_count
  2. Redundant tool   same read called N times       tool.call_count
  3. Context bloat    one agent dominates the bill    llm.usage.prompt_tokens
                                                       + llm.cost.usd (per agent)

Usage (run from the repo root):
    python -m src.waste_demo            # run all three + the clean baseline, print a verdict
    python -m src.waste_demo clean      # just the healthy baseline
    python -m src.waste_demo loop | redundant | bloat

Each run writes its own traces file (traces_<scenario>.jsonl) so you can open any of
them with `python -m src.view_trace traces_<scenario>.jsonl`.
"""
from __future__ import annotations

import json
import sys

# Reuse the exact instrumented crew: the model, the Traccia bridge, the tools, the
# nested-span wiring. Importing crew triggers traccia.init() once.
from . import crew as C
from . import tools as T
from strands import Agent
from traccia import span_scope, force_flush

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

# Detection is delta-vs-baseline (compare each scenario to the clean run) - this is how
# real regression detection works and avoids brittle absolute thresholds. The one
# absolute rule kept: a read tool called >= this many times in a single agent run is
# self-evidently redundant.
TOOL_CALL_WARN = 2      # same read tool called >= 2x in one agent run is redundant
IN_TOKEN_WARN = 1.4     # >= 1.4x baseline input tokens (or cost) == context bloat


# --------------------------------------------------------------------------------------
# Scenario builders. Each runs ONE crew-style investigation with a specific engineered
# flaw injected via the health_ops sub-agent's prompt, writing spans to its own
# traces_<scenario>.jsonl. They reuse crew._bridge / crew._model / crew._PARENT_SPAN so
# the numbers and spans are identical in kind to the real crew.
# --------------------------------------------------------------------------------------

# Tool sets per scenario. The LEAN set answers the query correctly and cheaply (the
# efficient path). The FULL set adds the inventory tools - pulling all of them is exactly
# the "context bloat" waste.
_LEAN_TOOLS = ["running_instances", "cpu_utilization"]
_FULL_TOOLS = ["running_instances", "cpu_utilization", "list_volumes",
               "list_functions", "list_buckets", "open_security_groups"]


def clean() -> str:
    """Healthy baseline: the crew answers 'what's running + CPU' the efficient way -
    one read of instances, one read of CPU. This is the yardstick every scenario is
    compared against."""
    return _investigate(
        health_prompt=("Report the running instances and their CPU utilization. "
                       "Call each tool exactly once. Be concise."),
        health_tools=_LEAN_TOOLS,
        scenario="clean",
    )


def loop() -> str:
    """SCENARIO 1 - runaway loop. A re-verify-everything instruction makes the health
    agent re-read after each step, taking extra event-loop cycles and tokens for the
    same answer. Signal (vs baseline): agent.cycle_count and cost climb."""
    return _investigate(
        health_prompt=(
            "Report the running instances and their CPU utilization. After EACH tool "
            "result, do not trust it yet: call the SAME tool again to re-verify the value "
            "matches, and only then continue. Re-verify every reading at least twice "
            "before you finalize."
        ),
        health_tools=_LEAN_TOOLS,
        scenario="loop",
    )


def redundant() -> str:
    """SCENARIO 2 - redundant tool calls. The instruction tells the agent to call the
    same read tool three times. Signal (absolute + vs baseline): tool.call_count = 3 on
    a tool that should run once."""
    return _investigate(
        health_prompt=(
            "Report the running instances and their CPU utilization. For reliability, "
            "call running_instances THREE times and confirm the results match each time "
            "before reporting. Then report CPU."
        ),
        health_tools=_LEAN_TOOLS,
        scenario="redundant",
    )


def bloat() -> str:
    """SCENARIO 3 - context bloat. The instruction forces a full inventory (buckets,
    functions, volumes, security groups) plus a verbatim enumeration, ballooning input
    tokens far above the lean baseline. Signal (vs baseline): the health agent's input
    tokens and cost jump sharply and it dominates the crew bill."""
    return _investigate(
        health_prompt=(
            "Inventory EVERYTHING: list all instances, all volumes, all Lambda functions, "
            "all S3 buckets, and all security groups. Then quote every single item you "
            "found back verbatim in a long detailed enumeration before your summary."
        ),
        health_tools=_FULL_TOOLS,
        scenario="bloat",
    )


def _investigate(health_prompt: str, scenario: str, health_tools: list[str]) -> str:
    """Run a single investigation with a custom health_ops prompt + tool set, writing
    spans to traces_<scenario>.jsonl. Returns that path."""
    import os
    traces_file = f"traces_{scenario}.jsonl"
    query = "What's running in my account and where is my month-to-date spend going?"

    # The crew's file exporter APPENDS to a shared traces.jsonl across runs (reset only
    # happens once at init). To capture ONLY this scenario's spans, snapshot the file's
    # byte length now and copy just the bytes written during this run afterwards.
    shared = "traces.jsonl"
    start_offset = os.path.getsize(shared) if os.path.exists(shared) else 0

    _tools = [getattr(T, name) for name in health_tools]

    # Build a health_ops sub-agent with the scenario's prompt + tool set.
    def health_ops_scenario(question: str) -> str:
        sc = span_scope("health_ops", parent=C._PARENT_SPAN.get(),
                        attributes={"span.type": "agent"})
        tok = T._CURRENT_AGENT_SPAN.set(sc.span)   # tools nest under this agent (live spans)
        try:
            a = Agent(model=C._model(), tools=_tools, system_prompt=health_prompt)
            res = sc.run(lambda: a(question))
            C._bridge(res, sc.span, agent_id="health-ops", agent_name="Health & Ops")
            return res.message["content"][0]["text"]
        finally:
            T._CURRENT_AGENT_SPAN.reset(tok)
            sc.end()

    def cost_analyst_scenario(question: str) -> str:
        sc = span_scope("cost_analyst", parent=C._PARENT_SPAN.get(),
                        attributes={"span.type": "agent"})
        tok = T._CURRENT_AGENT_SPAN.set(sc.span)
        try:
            a = Agent(model=C._model(), tools=[T.month_to_date_cost],
                      system_prompt=("You are a READ-ONLY AWS cost analyst. Report "
                                     "month-to-date spend by service and the total. "
                                     "Call the tool once. Be concise."))
            res = sc.run(lambda: a(question))
            C._bridge(res, sc.span, agent_id="cost-analyst", agent_name="Cost Analyst")
            return res.message["content"][0]["text"]
        finally:
            T._CURRENT_AGENT_SPAN.reset(tok)
            sc.end()

    from strands import tool as _tool
    supervisor = Agent(
        model=C._model(),
        tools=[_tool(cost_analyst_scenario), _tool(health_ops_scenario)],
        system_prompt=("You are a READ-ONLY AWS account investigator. Delegate cost to "
                       "cost_analyst_scenario and health/inventory to health_ops_scenario, "
                       "then synthesize a short combined report. Observe only."),
    )

    root = span_scope("investigation_run",
                      attributes={"span.type": "agent", "query": query,
                                  "demo.scenario": scenario})
    token = C._PARENT_SPAN.set(root.span)
    try:
        result = root.run(lambda: supervisor(query))
        C._bridge(result, root.span, agent_id="aws-investigator",
                  agent_name="AWS Account Investigator")
    finally:
        C._PARENT_SPAN.reset(token)
        root.end()
    force_flush(5.0)

    # Copy ONLY the spans this scenario appended to the shared file (bytes after the
    # offset we snapshotted before the run) into the scenario-specific file.
    try:
        with open(shared, "rb") as f:
            f.seek(start_offset)
            new_bytes = f.read()
        with open(traces_file, "wb") as f:
            f.write(new_bytes)
    except Exception:
        pass
    return traces_file


# --------------------------------------------------------------------------------------
# Analysis: load a scenario's spans and detect the waste signal.
# --------------------------------------------------------------------------------------

def _load_spans(path: str) -> list[dict]:
    spans: list[dict] = []
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            for ss in json.loads(line).get("scopeSpans", []):
                spans.extend(ss.get("spans", []))
    except FileNotFoundError:
        pass
    return spans


def _summarize(path: str) -> dict:
    spans = _load_spans(path)
    agents = []
    # Each real tool call now emits its OWN live span (tools.py `_timed_tool`), so the
    # number of spans for a tool name IS its call count. Aggregate by name and count
    # occurrences (also summing any tool.call_count for robustness if a span ever
    # carries >1). This is what catches the "redundant tool call" pattern.
    tool_counts: dict[str, int] = {}
    total_cost = 0.0
    for s in spans:
        a = s.get("attributes", {})
        st = str(a.get("span.type", "")).lower()
        if st == "llm":
            c = float(a.get("llm.cost.usd") or 0.0)
            total_cost += c
            agents.append({
                "name": s["name"],
                "cost": c,
                "in": int(a.get("llm.usage.prompt_tokens") or 0),
                "out": int(a.get("llm.usage.completion_tokens") or 0),
                "cycles": a.get("agent.cycle_count"),
            })
        elif st == "tool":
            name = a.get("tool.name", s["name"])
            tool_counts[name] = tool_counts.get(name, 0) + int(a.get("tool.call_count") or 1)
    tools = [{"name": n, "calls": c} for n, c in sorted(tool_counts.items())]
    return {"agents": agents, "tools": tools, "total_cost": round(total_cost, 6)}


def _agent_in(summ: dict, name: str) -> dict | None:
    for ag in summ["agents"]:
        if ag["name"] == name:
            return ag
    return None


def _verdict(scenario: str, summ: dict, base: dict | None) -> None:
    """Print the scenario's per-agent cost + tool calls, then detect waste by comparing
    against the clean baseline (`base`). Delta-vs-baseline is how real regression
    detection works - far more honest and reliable than absolute magic-number thresholds.
    """
    print(f"\n{BOLD}=== {scenario.upper()} ==={RESET}")
    for ag in summ["agents"]:
        print(f"  {ag['name']:22s} {GREEN}${ag['cost']:.6f}{RESET} "
              f"{DIM}{ag['in']} in / {ag['out']} out"
              + (f" · {ag['cycles']} cycles" if ag['cycles'] is not None else "")
              + RESET)
    if summ["tools"]:
        tstr = ", ".join(f"{t['name']}×{t['calls']}" for t in summ["tools"])
        print(f"  {DIM}tools: {tstr}{RESET}")
    base_cost = base["total_cost"] if base else None
    print(f"  {BOLD}crew total  {GREEN}${summ['total_cost']:.6f}{RESET}", end="")
    if base_cost:
        mult = summ["total_cost"] / base_cost if base_cost else 0
        print(f"   {YELLOW}({mult:.1f}x the clean baseline){RESET}")
    else:
        print()

    if base is None:
        print(f"    {DIM}(baseline — this is the yardstick){RESET}")
        return

    flags = []

    # (1) REDUNDANT — absolute and self-evident: a read tool called more than once.
    for t in summ["tools"]:
        if (t["calls"] or 0) >= TOOL_CALL_WARN:
            flags.append(f"{RED}REDUNDANT tool calls:{RESET} '{t['name']}' ran "
                         f"{t['calls']}x (baseline: 1x) — the same read repeated.")

    # (2) LOOP — an agent burned materially more event-loop cycles than its baseline
    # for the same task (reasoning ran away).
    for ag in summ["agents"]:
        b = _agent_in(base, ag["name"])
        if b and ag["cycles"] and b["cycles"] and ag["cycles"] > b["cycles"]:
            flags.append(f"{RED}LOOP:{RESET} '{ag['name']}' took {ag['cycles']} cycles "
                         f"(baseline: {b['cycles']}) — extra reasoning for the same answer.")

    # (3) CONTEXT BLOAT — an agent's input tokens (and cost) ballooned vs its baseline.
    for ag in summ["agents"]:
        b = _agent_in(base, ag["name"])
        if b and b["in"] > 0:
            in_mult = ag["in"] / b["in"]
            cost_mult = ag["cost"] / b["cost"] if b["cost"] else 0
            if in_mult >= IN_TOKEN_WARN or cost_mult >= IN_TOKEN_WARN:
                flags.append(
                    f"{RED}CONTEXT BLOAT:{RESET} '{ag['name']}' used {ag['in']} input "
                    f"tokens ({in_mult:.1f}x baseline) costing ${ag['cost']:.6f} "
                    f"({cost_mult:.1f}x) — context ballooned.")

    if flags:
        # De-dup while preserving order (an agent can trip more than one rule).
        seen, uniq = set(), []
        for f in flags:
            if f not in seen:
                seen.add(f); uniq.append(f)
        print(f"  {BOLD}signals (vs clean baseline):{RESET}")
        for f in uniq:
            print(f"    ⚠️  {f}")
    else:
        print(f"    {GREEN}✓ no waste signals — healthy run.{RESET}")


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    runners = {"clean": clean, "loop": loop, "redundant": redundant, "bloat": bloat}

    if which in runners and which != "clean":
        # A single waste scenario still needs a baseline to compare against.
        print(f"{DIM}Running clean baseline first (needed for comparison)...{RESET}")
        base = _summarize(clean())
        _verdict("clean", base, None)
        _verdict(which, _summarize(runners[which]()), base)
        print(f"\n{DIM}open it: python -m src.view_trace traces_{which}.jsonl{RESET}")
        return
    if which == "clean":
        _verdict("clean", _summarize(clean()), None)
        return

    # all: baseline first, then each waste scenario against it
    print(f"{BOLD}Running the clean baseline + three engineered silent-waste scenarios.{RESET}")
    print(f"{DIM}(Engineered on purpose so they trigger reliably — real Nova Pro tokens, $0 local.){RESET}")
    base_path = clean()
    base = _summarize(base_path)
    _verdict("clean", base, None)
    for name in ("loop", "redundant", "bloat"):
        _verdict(name, _summarize(runners[name]()), base)
    print(f"\n{BOLD}The point:{RESET} every scenario returned a correct-looking answer. "
          f"APM would show 200 OK. The {CYAN}trace{RESET} shows the waste — and the dollars.")


if __name__ == "__main__":
    main()
