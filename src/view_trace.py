"""Render the Traccia trace tree + per-agent cost from traces.jsonl.

This is the on-camera payoff view. It shows, for a real multi-agent run:
  - the nested tree (supervisor -> sub-agents -> the tools each one called),
  - real tokens, cost, latency and loop/cycle count per agent,
  - a per-tool table (call counts + time), and
  - the crew cost total.

Usage (run from the repo root):
    python -m src.view_trace [traces.jsonl]
"""
from __future__ import annotations

import json
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"


def load_spans(path: str) -> list[dict]:
    spans: list[dict] = []
    for line in open(path):
        if not line.strip():
            continue
        doc = json.loads(line)
        for ss in doc.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                spans.append(sp)
    return spans


def _dur_ms(s: dict) -> float:
    return (s["endTimeUnixNano"] - s["startTimeUnixNano"]) / 1e6


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "traces.jsonl"
    spans = load_spans(path)
    if not spans:
        print(f"No spans found in {path}. Run `python -m src.crew` first.")
        return

    by_id = {s["spanId"]: s for s in spans}
    children: dict[str, list[dict]] = {}
    for s in spans:
        children.setdefault(s.get("parentSpanId"), []).append(s)
    roots = [s for s in spans if s.get("parentSpanId") not in by_id]

    def show(s: dict, depth: int = 0) -> None:
        a = s.get("attributes", {})
        stype = a.get("span.type", "")
        pad = "    " * depth
        name = s["name"]
        dur = _dur_ms(s)

        if stype == "tool":
            calls = a.get("tool.call_count")
            t = a.get("tool.total_time_s")
            errs = a.get("tool.error_count") or 0
            status = f"{GREEN}ok{RESET}" if not errs else f"{YELLOW}{errs} err{RESET}"
            print(f"{pad}{DIM}|- {CYAN}{name}{RESET}  "
                  f"{DIM}calls={calls} time={t}s {status}{RESET}")
        else:
            inp = a.get("llm.usage.prompt_tokens")
            out = a.get("llm.usage.completion_tokens")
            cost = a.get("llm.cost.usd")
            cyc = a.get("agent.cycle_count")
            extra = ""
            if cost is not None:
                extra = (f"  {GREEN}${cost:.6f}{RESET}"
                         f"  {DIM}{inp} in / {out} out")
                if cyc is not None:
                    extra += f" · {cyc} cycles"
                extra += f" · {dur:.0f} ms{RESET}"
            print(f"{pad}{BOLD}|- {name}{RESET}{extra}")

        for c in sorted(children.get(s["spanId"], []),
                        key=lambda x: x["startTimeUnixNano"]):
            show(c, depth + 1)

    print(f"\n{BOLD}=== TRACE TREE ==={RESET}\n")
    for r in sorted(roots, key=lambda x: x["startTimeUnixNano"]):
        show(r)

    # Per-agent cost table + crew total
    print(f"\n{BOLD}=== PER-AGENT COST ==={RESET}")
    total = 0.0
    for s in sorted(spans, key=lambda x: x["startTimeUnixNano"]):
        a = s.get("attributes", {})
        if "llm.cost.usd" in a:
            c = a["llm.cost.usd"]
            total += c
            print(f"  {s['name']:20s} {GREEN}${c:.6f}{RESET}  "
                  f"{DIM}({a['llm.usage.prompt_tokens']} in / "
                  f"{a['llm.usage.completion_tokens']} out){RESET}")
    print(f"  {BOLD}{'CREW TOTAL':20s} {GREEN}${total:.6f}{RESET}")

    # Per-tool table
    tool_rows = []
    for s in spans:
        a = s.get("attributes", {})
        if a.get("span.type") == "tool":
            tool_rows.append((
                a.get("tool.name", s["name"]),
                a.get("tool.call_count"),
                a.get("tool.total_time_s"),
                a.get("tool.error_count") or 0,
            ))
    if tool_rows:
        print(f"\n{BOLD}=== TOOL CALLS (real AWS reads) ==={RESET}")
        print(f"  {'tool':22s} {'calls':>5s} {'time(s)':>9s} {'errors':>7s}")
        for name, calls, t, errs in sorted(tool_rows):
            print(f"  {name:22s} {str(calls):>5s} {str(t):>9s} {str(errs):>7s}")

    # Query + total wall time
    q = next((s["attributes"].get("query") for s in spans
              if s.get("attributes", {}).get("query")), None)
    if q:
        print(f"\n{BOLD}=== RUN ==={RESET}")
        print(f"  {DIM}query:{RESET} {q}")
        root_dur = max(_dur_ms(r) for r in roots)
        print(f"  {DIM}wall time:{RESET} {root_dur:.0f} ms   "
              f"{DIM}spans:{RESET} {len(spans)}")


if __name__ == "__main__":
    main()
