# Catch the Silent Waste: AI Agent Observability on AWS (Bedrock + Strands + Traccia)

> **Live demo (replay):** https://simplynadaf.github.io/ai-agent-observability-aws/
> A static, backend-free replay of a real run - agents light up, wires pulse, and the
> real report reveals. LIVE mode (real Bedrock + AWS reads) runs locally, see below.

Your multi-agent crew returned a **perfect answer** - and quietly cost **1.5x** what it
should have. Your APM dashboard says `200 OK`. The **trace** is the only place that extra
money is visible.

This repo builds a read-only **AWS Account Investigator** crew on **Amazon Nova Pro** (via
**AWS Strands Agents**), instruments it with **Traccia**, and then does the thing most
"agent observability" demos skip: it makes the crew **silently waste money three ways** and
shows you the exact trace signal that catches each one - in real dollars, on real AWS
reads, reproducible at **$0** locally.

```
=== CLEAN  vs  BLOAT ===   (same query, same crew, same read-only AWS - different bill)

  agent                          CLEAN                          BLOAT
  health_ops        $0.002463 2279in/200out 3c   $0.004802 2871in/783out 2c  <- 1.9x
  cost_analyst      $0.002578 1515in/427out 2c   $0.002578 1515in/427out 2c
  investigation_run $0.003090 1578in/571out 2c   $0.004985 2183in/1012out 2c <- 1.6x

  CLEAN total  $0.008131
  BLOAT total  $0.012366   (1.5x  +$0.004235)

  Both runs returned a correct-looking answer. APM shows 200 OK for both.
  The trace is the only place the extra $0.004235 is visible.
```

(Every number is REAL Nova Pro token usage; they vary run-to-run. `$0` local, read-only.)

## The three silent-waste scenarios

Multi-agent systems fail quietly. The research backs this up: the Multi-Agent System
Failure Taxonomy (MAST, arXiv:2503.13657) found a **41-86.7% failure rate** across 7
state-of-the-art multi-agent systems - and many failures are not crashes, they are runs
that *look* successful. `waste_demo.py` reproduces three of them deterministically and
catches each with a real Strands metric that our Traccia bridge stamps on the span:

| Scenario | What goes wrong | Trace signal | Attribute |
|---|---|---|---|
| **Runaway loop** | an agent re-reasons/re-reads for the same answer | cycle count climbs vs baseline | `agent.cycle_count` |
| **Redundant tool calls** | the same read runs 3x | call count > 1 | `tool.call_count` |
| **Context bloat** | one agent pulls far more context than needed | input tokens + cost balloon vs baseline | `llm.usage.prompt_tokens`, `llm.cost.usd` |

Detection is **delta-vs-baseline** (how real regression detection works), not brittle
magic-number thresholds.

```bash
python waste_demo.py                       # clean baseline + all 3 waste runs + verdict
python compare.py traces_clean.jsonl traces_bloat.jsonl   # the side-by-side "different bill" view
```

## Why this exists

Traditional APM tells you the service returned HTTP 200. It does **not** tell you:

- how many steps your agent took, or whether it looped,
- whether a "successful" run actually did the right thing,
- **which agent burned the tokens**, and what each step cost.

Agent observability answers those. This repo is a minimal, honest example on an
AWS-native stack - and unlike most demos, it shows observability *earning its keep* by
catching waste you would never see in APM.

## The crew (all read-only)

| Agent | Shows in dashboard as | Tools | AWS calls (read-only) |
|---|---|---|---|
| **supervisor** (`investigation_run`) | AWS Account Investigator | delegates to the three sub-agents | none directly |
| **cost_analyst** | Cost Analyst | `month_to_date_cost`, `cost_forecast` | `ce:GetCostAndUsage`, `ce:GetCostForecast` |
| **health_ops** | Health & Ops | `running_instances`, `cpu_utilization`, `list_volumes`, `list_functions`, `list_buckets` | `ec2:DescribeInstances`, `cloudwatch:GetMetricStatistics`, `ec2:DescribeVolumes`, `lambda:ListFunctions`, `s3:ListAllMyBuckets` |
| **security_ops** | Security Auditor | `open_security_groups`, `mfa_findings`, `public_s3_buckets`, `guardduty_enabled` | `ec2:DescribeSecurityGroups`, `iam:GetAccountSummary`, `iam:ListUsers`, `iam:ListMFADevices`, `s3:ListAllMyBuckets`, `s3:GetPublicAccessBlock`, `guardduty:ListDetectors` |

Every tool is describe/get only. Nothing is created, modified, or deleted. Attach a
read-only IAM role and the "can't touch anything" property is literally true.

The three sub-agents are a real separation of concerns (cost / health / security), not
padding. Each one stamps its own `agent.id` / `agent.name` on its span, so from a
**single crew run** the Traccia dashboard shows four distinct agents (Cost Analyst,
Health & Ops, Security Auditor, and the AWS Account Investigator supervisor), each with
its own token and cost profile. Every tool opens a **live span around the real boto3
call**, so the trace timeline shows each AWS read's true duration - not a 0ms marker.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# Requires AWS credentials with the read-only actions above, and Amazon Nova Pro
# enabled in Bedrock (us-east-1).
python crew.py            # runs the crew against real AWS, writes traces.jsonl
python view_trace.py      # renders the nested tree + per-agent cost table
```

No Traccia platform account or API key is needed: traces are written locally to
`traces.jsonl` via Traccia's file exporter, and cost is computed locally from a pricing
override. Total cost to run: a fraction of a cent of Nova Pro tokens.

## How the cost bridge works

Traccia is pure OpenTelemetry and auto-instruments several SDKs, but it does **not**
auto-instrument Bedrock/Strands. That is not a gap - it means you wire it in explicitly
with a few lines, and then it works with any stack. We read the real token usage off the
Strands result and stamp it onto the current span:

```python
u = result.metrics.accumulated_usage          # {'inputTokens', 'outputTokens', 'totalTokens'}
inp, out = u["inputTokens"], u["outputTokens"]
cost = inp/1000*PRICE_IN + out/1000*PRICE_OUT  # Nova Pro us-east-1
span.set_attribute("llm.cost.usd", round(cost, 8))
```

### Per-agent attribution, no double-counting

Strands' "agents-as-tools" runs each sub-agent in its **own event loop** with its **own**
metrics. A supervisor's `accumulated_usage` counts only the supervisor's own model calls -
it does **not** include the sub-agents' tokens. (This repo includes `probe_doublecount.py`,
which proves it: a sub-agent that emits ~227 output tokens does not inflate the
supervisor's output tokens.) So each level's real usage is attributed to its own span, and
the crew total is simply the sum. No delta subtraction, no double-count.

A teachable detail you can see in the tree: `health_ops` usually costs the most because
it chains the most tool calls (instances, CPU, volumes, functions, buckets), which feeds
more context back into the model - so its **input** tokens climb. `cost_analyst` and
`security_ops` each run a single focused read and cost less. Same crew, different
per-agent bill, all visible per agent in the trace.

## Files

| File | What it is |
|---|---|
| `tools.py` | Seven read-only AWS tools (EC2, CloudWatch, Cost Explorer, S3, Lambda, EBS volumes, security groups). |
| `crew.py` | The supervisor + sub-agents, Traccia init, and the cost bridge. |
| `waste_demo.py` | Runs the clean baseline + three engineered silent-waste scenarios and catches each with a delta-vs-baseline verdict. |
| `compare.py` | Side-by-side "same answer, different bill" view of a baseline run vs a waste run. |
| `view_trace.py` | Renders the nested trace tree and per-agent cost table from `traces.jsonl`. |
| `probe_multiagent.py` | Prints token usage at every level (supervisor + each sub-agent). |
| `probe_doublecount.py` | Proves supervisor usage is exclusive of sub-agent tokens. |

## Minimal read-only IAM policy

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
      "ec2:DescribeSecurityGroups",
      "cloudwatch:GetMetricStatistics",
      "ce:GetCostAndUsage",
      "s3:ListAllMyBuckets",
      "lambda:ListFunctions"
    ],
    "Resource": "*"
  }]
}
```

## Notes

- Model: `amazon.nova-pro-v1:0`. Pricing used: $0.0008 / 1K input, $0.0032 / 1K output
  (Amazon Nova Pro on-demand, us-east-1). Verify current pricing before relying on the
  dollar figures.
- Cost Explorer returns whatever your account actually spent this month; if the account
  has no spend, you will honestly see near-zero numbers.

## License

Apache-2.0. See [LICENSE](LICENSE).
