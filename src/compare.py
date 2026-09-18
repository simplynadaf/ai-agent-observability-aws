"""Side-by-side: a healthy baseline run vs a silent-waste run, from their trace files.

This is the article's signature visual - the "same answer, different bill" shot. It reads
two traces_*.jsonl files (produced by waste_demo.py), lines up the per-agent numbers, and
prints the delta plus the exact trace signal that explains the extra cost.

Usage:
    python compare.py traces_clean.jsonl traces_bloat.jsonl
    python compare.py traces_clean.jsonl traces_loop.jsonl
    python compare.py                      # defaults to clean vs bloat if both exist
"""
from __future__ import annotations

import json
import sys

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"


def _summarize(path: str) -> dict:
    agents: dict[str, dict] = {}
    tools: dict[str, int] = {}
    total = 0.0
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        for ss in json.loads(line).get("scopeSpans", []):
            for s in ss.get("spans", []):
                a = s.get("attributes", {})
                st = str(a.get("span.type", "")).lower()
                if st == "llm":
                    c = float(a.get("llm.cost.usd") or 0.0)
                    total += c
                    agents[s["name"]] = {
                        "cost": c,
                        "in": int(a.get("llm.usage.prompt_tokens") or 0),
                        "out": int(a.get("llm.usage.completion_tokens") or 0),
                        "cycles": a.get("agent.cycle_count"),
                    }
                elif st == "tool":
                    name = a.get("tool.name", s["name"])
                    tools[name] = tools.get(name, 0) + int(a.get("tool.call_count") or 0)
    return {"agents": agents, "tools": tools, "total": round(total, 6)}


def _fmt_cycles(c) -> str:
    return f"{c}c" if c is not None else "-"


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2:
        left_path, right_path = args
    else:
        left_path, right_path = "traces_clean.jsonl", "traces_bloat.jsonl"

    left = _summarize(left_path)
    right = _summarize(right_path)

    lname = left_path.replace("traces_", "").replace(".jsonl", "").upper()
    rname = right_path.replace("traces_", "").replace(".jsonl", "").upper()

    print(f"\n{BOLD}=== {lname}  vs  {rname} ==={RESET}")
    print(f"{DIM}(same query, same crew, same read-only AWS - different bill){RESET}\n")

    # Per-agent table
    names = list(dict.fromkeys(list(left["agents"]) + list(right["agents"])))
    print(f"  {'agent':20s} {lname:>26s}   {rname:>26s}")
    print(f"  {'-'*20} {'-'*26}   {'-'*26}")
    for n in names:
        l = left["agents"].get(n)
        r = right["agents"].get(n)
        lc = f"${l['cost']:.6f} {l['in']}in/{l['out']}out {_fmt_cycles(l['cycles'])}" if l else "-"
        rc = f"${r['cost']:.6f} {r['in']}in/{r['out']}out {_fmt_cycles(r['cycles'])}" if r else "-"
        # Color the right side red if this agent got materially more expensive.
        mark = ""
        if l and r and l["cost"] > 0 and r["cost"] / l["cost"] >= 1.4:
            mark = f" {RED}<- {r['cost']/l['cost']:.1f}x{RESET}"
        print(f"  {n:20s} {DIM}{lc:>26s}{RESET}   {rc:>26s}{mark}")

    # Tools
    print(f"\n  {BOLD}tool calls:{RESET}")
    all_tools = list(dict.fromkeys(list(left["tools"]) + list(right["tools"])))
    for t in all_tools:
        lc = left["tools"].get(t, 0)
        rc = right["tools"].get(t, 0)
        mark = f"  {RED}<- {rc}x (was {lc}x){RESET}" if rc > lc and rc >= 2 else ""
        print(f"    {t:24s} {lname}: {lc}   {rname}: {rc}{mark}")

    # Totals + verdict
    print()
    print(f"  {BOLD}{lname} total{RESET}  {GREEN}${left['total']:.6f}{RESET}")
    mult = right["total"] / left["total"] if left["total"] else 0
    delta = right["total"] - left["total"]
    color = RED if mult >= 1.3 else GREEN
    print(f"  {BOLD}{rname} total{RESET}  {color}${right['total']:.6f}{RESET}  "
          f"{color}({mult:.1f}x  +${delta:.6f}){RESET}")

    print(f"\n  {BOLD}The point:{RESET} both runs returned a correct-looking answer. "
          f"APM would show {GREEN}200 OK{RESET} for both.")
    print(f"  The {CYAN}trace{RESET} is the only place the extra "
          f"{color}${delta:.6f}{RESET} ({mult:.1f}x) is visible.\n")


if __name__ == "__main__":
    main()
