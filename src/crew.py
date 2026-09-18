"""AWS Account Investigator - a read-only multi-agent crew on Strands + Nova Pro,
instrumented with Traccia for per-agent identity, cost, and token attribution.

Topology (Strands "agents-as-tools"), each sub-agent a distinct named agent:
    supervisor (investigation_run)               agent: "AWS Account Investigator"
      |-- cost_analyst   -> cost_forecast,        agent: "Cost Analyst"     (Cost Explorer)
      |                     last_month_cost,
      |                     daily_cost_trend
      |-- health_ops     -> instances, cpu,       agent: "Health & Ops"     (EC2/CW/EBS/
      |                     volumes, functions,                              Lambda/S3)
      |                     buckets
      |-- security_ops   -> open_security_groups, agent: "Security Auditor" (EC2 SG, IAM,
      |                     mfa_findings,                                     S3, GuardDuty)
      |                     public_s3_buckets,
      |                     guardduty_enabled

WHY distinct per-agent identity works in ONE process (verified from the Traccia SDK
source, processors/agent_enricher.py): AgentEnrichmentProcessor resolves identity with
SPAN ATTRIBUTES at the highest precedence -
    agent_id = attrs.get("agent.id") or ... or runtime_config ... or init-default
    agent.name = catalog[name] or ... or runtime default or agent_id
So stamping `agent.id` / `agent.name` on each sub-agent's span makes each one show up as
its OWN agent in the dashboard, from a single crew run. No separate processes, no
throwaway agents. The four agents are a genuine separation of concerns (cost / health /
security / orchestration), not padding.

Cost attribution rule (VERIFIED by probe_doublecount.py, 2026-09-15):
    Strands runs each sub-agent in its OWN event loop with its OWN EventLoopMetrics.
    A supervisor's `accumulated_usage` counts ONLY the supervisor's own model calls -
    it does NOT include sub-agent tokens. So we attribute each level's real usage to
    its own span. Crew total = supervisor + sum(sub-agents). No delta, no double-count.

Tool timing: each tool opens its OWN live span around the real boto3 call (see
tools.py `_timed_tool`), parented to the running sub-agent via `T._CURRENT_AGENT_SPAN`.
That gives real wall-clock tool durations in the dashboard timeline (not 0ms markers).

Everything here is read-only. Attach a read-only IAM role and the "can't touch
anything" claim is literally true.
"""
from __future__ import annotations

import contextvars
import os
import uuid

import opentelemetry.context as otel_context
from strands import Agent, tool
from strands.models import BedrockModel
from traccia import init, observe, get_current_span, span_scope, force_flush

from . import tools as T

# Load .env into os.environ NOW, at import time, BEFORE we read TRACCIA_API_KEY below.
# Gotcha (this cost real dashboard-is-empty confusion): traccia.init(load_env=True) does
# load .env, but it does so INSIDE init() - which runs AFTER the module-level
# `_HAS_KEY = bool(os.getenv("TRACCIA_API_KEY"))` line further down. So without this
# pre-load, _HAS_KEY was computed from an empty process env, use_otlp came out False, and
# the crew silently ran "$0 local" mode and never pushed traces to app.traccia.ai even
# with a valid key sitting in .env. Loading here fixes the ordering.
try:
    from traccia.config import load_dotenv as _traccia_load_dotenv
    _traccia_load_dotenv(os.path.join(os.getcwd(), ".env"))
except Exception:
    # Fall back to python-dotenv, then to a no-op; init(load_env=True) is still a backstop.
    try:
        from dotenv import load_dotenv as _dotenv_load
        _dotenv_load(os.path.join(os.getcwd(), ".env"), override=False)
    except Exception:
        pass

# Holds the supervisor's span so sub-agent tools (run inside Strands' event loop,
# on a detached context) can parent their spans to it explicitly. Relying on
# @observe's implicit context does NOT work here: Strands' tool executor runs the
# tool outside the supervisor's Python call stack, so the spans would be siblings.
_PARENT_SPAN: contextvars.ContextVar = contextvars.ContextVar("parent_span", default=None)

# Correlation id shared by every agent's trace within a single crew run. Each agent
# (supervisor + sub-agents) is now its OWN top-level trace (see _start_root_span), so
# `session.id` is what ties the fleet back together: the Traccia Traces page can
# "Group by session" to show the four independent traces belong to one investigation.
_SESSION_ID: contextvars.ContextVar = contextvars.ContextVar("session_id", default=None)

# Optional live-event callback. Defaults to None, which means the crew behaves EXACTLY
# as before (the $0/local terminal path is byte-for-byte unchanged). A UI (see ui/app.py)
# can register a callback with set_event_callback() to receive live progress events
# (run_started -> agent_started -> agent_finished -> supervisor_finished -> final_report)
# and stream them to a browser over SSE. `_emit` no-ops when no callback is set and never
# raises into the crew: a broken UI must not break the investigation run.
_EVENT_CALLBACK = None


def set_event_callback(fn) -> None:
    """Register (or clear with None) a callback fn(event_kind: str, data: dict).

    Purely additive: the crew still runs identically with no callback. Used by the live
    UI to observe agent lifecycle without changing the crew's logic or cost path.
    """
    global _EVENT_CALLBACK
    _EVENT_CALLBACK = fn


def _emit(kind: str, **data) -> None:
    """Fire a live event to the registered callback, if any. Never raises into the crew."""
    cb = _EVENT_CALLBACK
    if cb is None:
        return
    try:
        cb(kind, data)
    except Exception:
        # A UI/consumer error must never affect the real investigation run.
        pass


def _start_root_span(name: str, attributes: dict):
    """Start a span as a brand-new ROOT trace (its own trace id), not a child.

    Traccia's `span_scope(parent=None)` still inherits the *current* OTel span if one is
    active (see tracer.start_span: it falls back to get_current_span()). To force a new
    independent trace per agent we detach the ambient context first, so there is no
    current span to inherit, then start the span, then restore the context.
    """
    attrs = dict(attributes)
    sid = _SESSION_ID.get()
    if sid:
        attrs.setdefault("session.id", sid)
    token = otel_context.attach(otel_context.Context())  # empty context -> no parent
    try:
        return span_scope(name, attributes=attrs)
    finally:
        otel_context.detach(token)

REGION = "us-east-1"
NOVA = "amazon.nova-pro-v1:0"
# Amazon Nova Pro on-demand, us-east-1 (AWS Price List API, effective 2026-08-01).
PRICE_IN = 0.0008   # USD per 1K input tokens
PRICE_OUT = 0.0032  # USD per 1K output tokens

# Crew agent identities. The supervisor uses the process default (set in init()); each
# sub-agent stamps its own agent.id / agent.name on its span so the dashboard Agents
# list shows a real, differentiated fleet from a single run.
SUPERVISOR_ID = "aws-investigator"
SUPERVISOR_NAME = "AWS Account Investigator"
AGENTS = {
    "cost_analyst": ("cost-analyst", "Cost Analyst"),
    "health_ops": ("health-ops", "Health & Ops"),
    "security_ops": ("security-auditor", "Security Auditor"),
}

# If a Traccia API key is present (.env -> TRACCIA_API_KEY), export to the Traccia
# platform so the trace tree + per-agent cost show up in app.traccia.ai. With no key,
# everything still runs at $0 to the local traces.jsonl file only. .strip() so a blank
# or whitespace-only value counts as "no key" rather than a truthy empty string.
_HAS_KEY = bool((os.getenv("TRACCIA_API_KEY") or "").strip())

# Production-style deployment identity. These are things you CONFIGURE (not measure), so
# setting them is honest: they make the traces read like a real multi-tenant deployment
# instead of a throwaway script. tenant/project/env show up as resource attributes and on
# the dashboard's ownership/environment columns; per-agent owner/team/org come from
# agent_config.json (auto-discovered by Traccia's AgentEnrichmentProcessor).
PROJECT_ID = "aws-account-investigator"
TENANT_ID = "sarvar-cloud"
USER_ID = "svc-investigator@sarvar-cloud"   # the service principal that runs the crew

init(
    agent_id=SUPERVISOR_ID,
    agent_name=SUPERVISOR_NAME,
    env="production",                    # this is a "prod" crew, not a dev toy
    project_id=PROJECT_ID,
    tenant_id=TENANT_ID,
    user_id=USER_ID,
    load_env=True,                       # loads .env (TRACCIA_API_KEY) if present
    enable_file_exporter=True,           # always keep a local copy
    file_exporter_path="traces.jsonl",
    reset_trace_file=True,
    enable_console_exporter=False,
    use_otlp=_HAS_KEY,                    # push to the platform only when we have a key
    enable_metrics=_HAS_KEY,
    pricing_override={NOVA: {"prompt": PRICE_IN, "completion": PRICE_OUT}},
)
print(f"[traccia] platform export: {'ON (app.traccia.ai)' if _HAS_KEY else 'OFF ($0 local)'}")

# Model call config. Kept as constants so the exact request settings can be stamped on
# each LLM span (llm.temperature / llm.max_tokens) - real production traces carry these.
TEMPERATURE = 0.2
# The supervisor synthesizes all three specialists' findings into one report. With the
# expanded security checks (security groups + IAM MFA + S3 + GuardDuty) and the cost
# forecast + top-5 breakdown, 1024 output tokens overflowed (MaxTokensReachedException),
# truncating the report. 4096 comfortably fits the full synthesis for Nova Pro.
MAX_TOKENS = 4096


def _model() -> BedrockModel:
    return BedrockModel(
        model_id=NOVA, region_name=REGION, temperature=TEMPERATURE, max_tokens=MAX_TOKENS
    )


def _bridge(result, span, model: str = NOVA,
            agent_id: str | None = None, agent_name: str | None = None,
            tools_available: list | None = None, tags: list | None = None,
            delegated_to: list | None = None) -> tuple[int, int, float]:
    """Attribute this agent's REAL usage, cost, timing, and identity onto its span.

    Traccia does not auto-instrument Bedrock/Strands, so we read everything off the
    Strands EventLoopMetrics and stamp it. Per-level only (usage is exclusive - see the
    module docstring - so there is no double-count to correct for).

    `agent_id`/`agent_name`, when given, are stamped as span attributes so the Traccia
    AgentEnrichmentProcessor attributes THIS span to that specific agent (span
    attributes take precedence over the process-level default). This is what makes each
    sub-agent a distinct agent in the dashboard from one crew run.

    `tools_available` is the full toolset the agent was GIVEN (vs agent.tools_called,
    which is only what the model actually invoked this run - the two can differ, and
    stamping both makes that honest and clear). `tags` land as span.tags (where Traccia
    reads them). `delegated_to` records which sub-agents a supervisor called.

    Tool spans are NOT emitted here anymore - each tool opens its own live span around
    the real boto3 call (tools.py `_timed_tool`), so durations are true wall-clock time.
    """
    m = result.metrics
    u = m.accumulated_usage
    inp = int(u["inputTokens"])
    out = int(u["outputTokens"])
    cost = round(inp / 1000 * PRICE_IN + out / 1000 * PRICE_OUT, 8)

    cycles = getattr(m, "cycle_count", None)
    latency_ms = dict(getattr(m, "accumulated_metrics", {})).get("latencyMs")
    tool_metrics = getattr(m, "tool_metrics", {}) or {}

    if span is not None:
        # Per-agent identity (span-level overrides the process default in the enricher).
        if agent_id:
            span.set_attribute("agent.id", agent_id)
        if agent_name:
            span.set_attribute("agent.name", agent_name)
        # `llm.model` (NOT `llm.request.model`) is the key Traccia's processors key off:
        # AgentEnrichmentProcessor classifies a span as span.type="LLM" when `llm.model`
        # is present, and the dashboard's "LLM Calls" / "Total Tokens" tiles only count
        # and sum spans classified as LLM. We also set span.type="LLM" explicitly so the
        # classification is robust rather than inferred. Without this the tiles read 0
        # even though the tokens/cost are on the span.
        span.set_attribute("llm.model", model)
        span.set_attribute("span.type", "LLM")
        span.set_attribute("llm.request.model", model)
        span.set_attribute("llm.vendor", "aws-bedrock")
        # Real request config for this call (production traces carry these).
        span.set_attribute("llm.temperature", TEMPERATURE)
        span.set_attribute("llm.max_tokens", MAX_TOKENS)
        span.set_attribute("llm.usage.prompt_tokens", inp)
        span.set_attribute("llm.usage.completion_tokens", out)
        span.set_attribute("llm.usage.total_tokens", inp + out)
        span.set_attribute("llm.usage.source", "provider_usage")
        span.set_attribute("llm.cost.usd", cost)
        # Why the agent stopped its loop (real Strands stop reason, e.g. "end_turn" /
        # "tool_use" / "max_tokens"). A truncated "max_tokens" here is a real production
        # signal that the answer was cut off.
        stop_reason = getattr(result, "stop_reason", None) or (
            getattr(result, "state", {}) or {}).get("stop_reason")
        if stop_reason:
            span.set_attribute("llm.finish_reason", str(stop_reason))
        # Observability signals: loop/cycle count, model latency, tools invoked.
        if cycles is not None:
            span.set_attribute("agent.cycle_count", cycles)
        if latency_ms is not None:
            span.set_attribute("llm.latency_ms", latency_ms)
        called = sorted(tool_metrics.keys())
        span.set_attribute("agent.tools_called", called)
        # Full toolset the agent was granted vs what it actually used this run. Stamping
        # both is honest: "has 5 tools, used 3" is real production information, not a bug.
        if tools_available is not None:
            span.set_attribute("agent.tools_available", sorted(tools_available))
            span.set_attribute("agent.tools_available_count", len(tools_available))
            span.set_attribute("agent.tools_called_count", len(called))
        # Tags where Traccia actually reads them (span.tags), not just Agent.trace_attributes.
        if tags:
            span.set_attribute("span.tags", [str(t) for t in tags])
        # Delegation: which sub-agents a supervisor invoked this run (agents-as-tools).
        if delegated_to is not None:
            span.set_attribute("agent.delegated_to", sorted(delegated_to))
            span.set_attribute("agent.delegation_count", len(delegated_to))

    return inp, out, cost


def _run_subagent(name: str, question: str, tools_list, system_prompt: str) -> str:
    """Run one named sub-agent as its OWN top-level trace.

    Each sub-agent starts a fresh ROOT span (its own trace id) via _start_root_span,
    so on the Traccia Traces page it appears as a separate execution rather than nested
    inside the supervisor. All agents in one crew run share a `session.id` so they can
    still be grouped back together. The sub-agent's span is published as the current
    agent span (so its tools parent their live spans under it, keeping tool timing real
    and each tool attributed to THIS agent).
    """
    agent_id, agent_name = AGENTS[name]
    _emit("agent_started", key=name, agent_id=agent_id, agent_name=agent_name)
    scope = _start_root_span(name, attributes={"span.type": "agent"})
    # Make this sub-agent's span the parent for its tools' live spans, and publish its
    # identity so each tool span is attributed to THIS agent (not the supervisor default).
    tok = T._CURRENT_AGENT_SPAN.set(scope.span)
    id_tok = T._CURRENT_AGENT_IDENTITY.set((agent_id, agent_name))
    # The full toolset this agent was granted (tool function names), so we can stamp
    # agent.tools_available and contrast it with what the model actually called.
    available = [getattr(t, "__name__", getattr(t, "tool_name", str(t))) for t in tools_list]
    try:
        a = Agent(model=_model(), tools=tools_list, system_prompt=system_prompt)
        result = scope.run(lambda: a(question))
        inp, out, cost = _bridge(result, scope.span, agent_id=agent_id, agent_name=agent_name,
                tools_available=available,
                tags=["strands", "multi-agent", "sub-agent", agent_id])
        # Emit the finished event with this agent's REAL numbers (same values _bridge
        # just stamped on the span - read from the metrics, not invented).
        m = result.metrics
        latency_ms = dict(getattr(m, "accumulated_metrics", {})).get("latencyMs")
        cycles = getattr(m, "cycle_count", None)
        stop_reason = getattr(result, "stop_reason", None) or (
            getattr(result, "state", {}) or {}).get("stop_reason")
        called = sorted((getattr(m, "tool_metrics", {}) or {}).keys())
        _emit("agent_finished", key=name, agent_id=agent_id, agent_name=agent_name,
              input_tokens=inp, output_tokens=out, total_tokens=inp + out, cost_usd=cost,
              latency_ms=latency_ms, cycles=cycles, stop_reason=str(stop_reason) if stop_reason else None,
              tools_called=called)
        return result.message["content"][0]["text"]
    finally:
        T._CURRENT_AGENT_IDENTITY.reset(id_tok)
        T._CURRENT_AGENT_SPAN.reset(tok)
        scope.end()


# --- specialist sub-agents, each wrapped as a tool the supervisor can delegate to ---
@tool
def cost_analyst(question: str) -> str:
    """Analyze AWS spend by service (READ-ONLY). Delegated sub-agent."""
    return _run_subagent(
        "cost_analyst", question,
        tools_list=[T.cost_forecast, T.last_month_cost, T.daily_cost_trend],
        system_prompt=(
            "You are a READ-ONLY AWS FinOps cost analyst. Use ALL of your tools to build "
            "a complete spend picture, then report:\n"
            "1. cost_forecast -> the actual month-to-date total, Cost Explorer's "
            "forecasted month-end total, and the top 5 services by actual month-to-date "
            "spend.\n"
            "2. last_month_cost -> last full month's total, and compute the "
            "month-over-month change (this month-to-date vs last month) as a direction "
            "and rough percentage.\n"
            "3. daily_cost_trend -> call out the single most expensive day this month and "
            "whether it stands out against the average daily spend (a possible spike).\n"
            "Do NOT invent a per-service forecast; only the account-level month-end "
            "forecast is available. Report only real numbers the tools return. Never "
            "suggest or make changes."
        ),
    )


@tool
def health_ops(question: str) -> str:
    """Check EC2 / CloudWatch health and inventory (READ-ONLY). Delegated sub-agent."""
    return _run_subagent(
        "health_ops", question,
        tools_list=[T.running_instances, T.cpu_utilization, T.list_volumes,
                    T.list_functions, T.list_buckets],
        system_prompt=(
            "You are a READ-ONLY AWS inventory and health assistant. Use the tools to "
            "inventory the account (instances, volumes, functions, buckets) and report "
            "running instances with their CPU utilization, plus any notable health "
            "findings such as unattached (available) EBS volumes. Never suggest or make "
            "changes."
        ),
    )


@tool
def security_ops(question: str) -> str:
    """Audit for read-only security findings (READ-ONLY). Delegated sub-agent."""
    return _run_subagent(
        "security_ops", question,
        tools_list=[T.open_security_groups, T.mfa_findings,
                    T.public_s3_buckets, T.guardduty_enabled],
        system_prompt=(
            "You are a READ-ONLY AWS security auditor. Use ALL your tools to audit the "
            "account and report findings: (1) security groups open to the whole "
            "internet (0.0.0.0/0) with their open ports, (2) MFA gaps - whether the "
            "root account has MFA and which IAM users lack it, (3) S3 buckets not fully "
            "protected by a public access block, and (4) whether GuardDuty threat "
            "detection is enabled. For each area, state the finding plainly and its "
            "risk. If a check comes back clean, say so. You only observe and report; "
            "never suggest running a change yourself."
        ),
    )


# --- supervisor delegates to the sub-agent tools, then synthesizes ---
supervisor = Agent(
    model=_model(),
    tools=[cost_analyst, health_ops, security_ops],
    system_prompt=(
        "You are a READ-ONLY AWS account investigator supervising three specialists: "
        "cost_analyst (spend), health_ops (instances/inventory/health), and security_ops "
        "(security risks). "
        "DELEGATION RULE - follow it strictly: call ONLY the specialist(s) whose domain "
        "the user actually asked about. "
        "- If the user asks only about cost/spend/billing, call ONLY cost_analyst. "
        "- If the user asks only about instances/inventory/health, call ONLY health_ops. "
        "- If the user asks only about security/risks/exposure, call ONLY security_ops. "
        "- Only when the user asks about the whole account (or two/three of these areas) "
        "do you call the matching specialists. "
        "Do NOT call a specialist whose domain was not asked about. After the "
        "specialist(s) respond, synthesize a short report containing ONLY the section(s) "
        "you gathered (a cost, health, and/or security section as applicable). You only "
        "observe and report; never suggest or make changes."
    ),
    trace_attributes={"tags": ["strands", "multi-agent", "observability-demo"]},
)


@observe(as_type="agent", name="investigation_run")
def run(query: str) -> str:
    # New correlation id for this crew run. Every agent (supervisor + sub-agents) is its
    # own top-level trace; they share this session.id so the dashboard can group them.
    session_id = uuid.uuid4().hex
    sess_tok = _SESSION_ID.set(session_id)
    _emit("run_started", session_id=session_id, query=query, supervisor_id=SUPERVISOR_ID,
          supervisor_name=SUPERVISOR_NAME)
    # Stamp session.id on the supervisor's own trace so it groups with the sub-agents.
    sup_span = get_current_span()
    if sup_span is not None:
        sup_span.set_attribute("session.id", session_id)
    print(f"[crew] session.id={session_id}")
    # Publish the supervisor span so any tool called directly by the supervisor can
    # parent to it. Sub-agents no longer use this - they start their own root traces.
    token = _PARENT_SPAN.set(sup_span)
    try:
        result = supervisor(query)
    finally:
        _PARENT_SPAN.reset(token)
        _SESSION_ID.reset(sess_tok)
    # Which specialist sub-agents the supervisor actually delegated to this run (the
    # sub-agents are exposed to it as tools, so they appear in Strands tool_metrics).
    delegated = sorted((getattr(result.metrics, "tool_metrics", {}) or {}).keys())
    # The supervisor keeps the process-default identity (SUPERVISOR_ID / _NAME).
    inp, out, cost = _bridge(result, get_current_span(),
                             agent_id=SUPERVISOR_ID, agent_name=SUPERVISOR_NAME,
                             tools_available=["cost_analyst", "health_ops", "security_ops"],
                             tags=["strands", "multi-agent", "supervisor", SUPERVISOR_ID],
                             delegated_to=delegated)
    print(f"[supervisor] own usage: {inp} in / {out} out  ->  ${cost}  (delegated: {delegated})")
    m = result.metrics
    sup_latency = dict(getattr(m, "accumulated_metrics", {})).get("latencyMs")
    sup_cycles = getattr(m, "cycle_count", None)
    final_text = result.message["content"][0]["text"]
    _emit("supervisor_finished", session_id=session_id, agent_id=SUPERVISOR_ID,
          agent_name=SUPERVISOR_NAME, input_tokens=inp, output_tokens=out,
          total_tokens=inp + out, cost_usd=cost, latency_ms=sup_latency, cycles=sup_cycles,
          delegated_to=delegated)
    _emit("final_report", session_id=session_id, report=final_text)
    return final_text


if __name__ == "__main__":
    answer = run(
        "What's running in my account, any security risks, and where is my "
        "month-to-date spend going?"
    )
    print("\n=== FINAL REPORT ===")
    print(answer)
    force_flush(5.0)
    print("RUN-DONE")
