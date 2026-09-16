"""Read-only AWS tools for the AWS Account Investigator crew.

Every function here is describe/get ONLY. Nothing creates, modifies, or deletes
anything. This is what makes the "can't touch anything" claim literally true on
camera: attach a read-only IAM role and these are the only AWS calls made.

All reads are REAL (no placeholders). If a metric has no data, we return an honest
zero rather than inventing a number.

Each tool wraps its real boto3 call in a LIVE Traccia span (`_timed_tool`) so the
span's duration is the true wall-clock time of the AWS read. The span is parented to
whichever sub-agent is currently running (published in `_CURRENT_AGENT_SPAN` by
crew.py), so the trace tree nests correctly: agent -> tool:<name> with a real bar in
the dashboard timeline instead of a 0ms marker.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import time
from contextlib import contextmanager

import boto3
from strands import tool
from traccia import span_scope

REGION = "us-east-1"

# Published by crew.py before each sub-agent runs. Holds that sub-agent's Traccia span
# so a tool invoked inside the agent's event loop can parent its own span to it. None
# means "no agent scope" (e.g. a tool called directly in a test) -> the tool span
# simply parents to whatever OTel span is current.
_CURRENT_AGENT_SPAN: contextvars.ContextVar = contextvars.ContextVar(
    "current_agent_span", default=None
)

# Published by crew.py alongside _CURRENT_AGENT_SPAN: the (agent_id, agent_name) of the
# sub-agent that is currently running. A tool span stamps these on itself so it is
# attributed to the sub-agent that actually invoked the tool, NOT the process-default
# supervisor identity. Without this, Traccia's AgentEnrichmentProcessor falls back to the
# init()-level default (aws-investigator) for every tool span, so a dashboard grouped by
# agent.id shows all tool work under the supervisor and the specialists look trace-less.
# None means "no agent scope" -> leave identity to the enricher default.
_CURRENT_AGENT_IDENTITY: contextvars.ContextVar = contextvars.ContextVar(
    "current_agent_identity", default=None
)


@contextmanager
def _timed_tool(name: str):
    """Open a live child span around a real AWS read so its duration is wall-clock true.

    Unlike a span reconstructed after the fact from Strands' tool_metrics (which ends
    up 0ms), this span is opened BEFORE the boto3 call and closed AFTER it, so the
    dashboard timeline shows the real time the AWS API took. On an exception the span
    is ended with the error recorded, then the exception re-raises.
    """
    scope = span_scope(
        f"tool:{name}",
        parent=_CURRENT_AGENT_SPAN.get(),
        attributes={
            "span.type": "tool", "tool.name": name, "tool.call_count": 1,
            # Cloud context - real production AWS tool spans carry where they ran.
            "cloud.provider": "aws", "cloud.region": REGION, "aws.region": REGION,
            "tool.kind": "aws.read_only",
        },
    )
    # Attribute this tool span to the sub-agent that invoked it (published by crew.py).
    # Span attributes take precedence over the process-level default in Traccia's
    # AgentEnrichmentProcessor, so this makes the tool show up under the right agent
    # (e.g. cost-analyst) instead of the supervisor default (aws-investigator).
    identity = _CURRENT_AGENT_IDENTITY.get()
    if identity is not None:
        agent_id, agent_name = identity
        if agent_id:
            scope.span.set_attribute("agent.id", agent_id)
        if agent_name:
            scope.span.set_attribute("agent.name", agent_name)
    started = time.perf_counter()
    try:
        yield scope
        scope.span.set_attribute("tool.total_time_s", round(time.perf_counter() - started, 4))
        scope.span.set_attribute("tool.success_count", 1)
        scope.span.set_attribute("tool.error_count", 0)
        scope.end()
    except Exception as exc:  # noqa: BLE001 - record then re-raise
        scope.span.set_attribute("tool.total_time_s", round(time.perf_counter() - started, 4))
        scope.span.set_attribute("tool.success_count", 0)
        scope.span.set_attribute("tool.error_count", 1)
        scope.end(error=exc)
        raise


@tool
def running_instances(region: str = REGION) -> list[dict]:
    """List running EC2 instances (id and type) in a region. READ-ONLY.

    Args:
        region: AWS region to inspect. Defaults to us-east-1.

    Returns:
        A list of dicts like {"id": "i-...", "type": "t3.medium"}.
    """
    with _timed_tool("running_instances"):
        ec2 = boto3.client("ec2", region_name=region)
        r = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )
        return [
            {"id": i["InstanceId"], "type": i["InstanceType"]}
            for res in r["Reservations"]
            for i in res["Instances"]
        ]


@tool
def cpu_utilization(instance_id: str, region: str = REGION) -> float:
    """Average CPU %% over the last hour for an instance, from CloudWatch. READ-ONLY.

    Args:
        instance_id: The EC2 instance id, e.g. "i-0123456789abcdef0".
        region: AWS region the instance runs in. Defaults to us-east-1.

    Returns:
        Average CPU utilization as a percent (0.0 if CloudWatch has no datapoint).
    """
    with _timed_tool("cpu_utilization"):
        cw = boto3.client("cloudwatch", region_name=region)
        end = dt.datetime.utcnow()
        start = end - dt.timedelta(hours=1)
        r = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Average"],
        )
        pts = r.get("Datapoints", [])
        return round(pts[0]["Average"], 2) if pts else 0.0


@tool
def month_to_date_cost(region: str = REGION) -> dict:
    """Month-to-date unblended cost (USD) by service, from Cost Explorer. READ-ONLY.

    Cost Explorer is a global service reached through the us-east-1 endpoint, so the
    `region` argument is accepted for a consistent tool signature but the CE client
    always uses us-east-1.

    Args:
        region: Accepted for signature consistency; CE always queries globally.

    Returns:
        A dict mapping service name -> month-to-date unblended cost in USD.
    """
    with _timed_tool("month_to_date_cost"):
        ce = boto3.client("ce", region_name="us-east-1")
        today = dt.date.today()
        first = today.replace(day=1)
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": first.isoformat(), "End": today.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        groups = r["ResultsByTime"][0]["Groups"]
        return {
            g["Keys"][0]: round(float(g["Metrics"]["UnblendedCost"]["Amount"]), 6)
            for g in groups
        }


@tool
def cost_forecast(region: str = REGION) -> dict:
    """Forecasted month-end cost (USD), overall and for the top 5 services. READ-ONLY.

    Combines actual month-to-date spend with Cost Explorer's forecast for the rest of
    the month, so the numbers line up with the AWS console's "Forecasted month end".

    Cost Explorer is global; the CE client always uses us-east-1 regardless of `region`.

    Args:
        region: Accepted for signature consistency; CE always queries globally.

    Returns:
        A dict with:
          - month_to_date_total: actual MTD unblended cost so far (USD)
          - forecast_month_end_total: forecasted total for the whole month (USD)
          - top_services: list of the top 5 services by MTD spend, each with
            {service, month_to_date, forecast_month_end}. Per-service forecast is the
            service's MTD scaled to the full month (CE only forecasts the account total,
            not per service), and is labelled as an estimate.
    """
    with _timed_tool("cost_forecast"):
        ce = boto3.client("ce", region_name="us-east-1")
        today = dt.date.today()
        first = today.replace(day=1)
        # first day of next month (exclusive end for the forecast window)
        next_month = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

        # 1) actual MTD by service (end is exclusive; use tomorrow to include today)
        tomorrow = today + dt.timedelta(days=1)
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": first.isoformat(), "End": tomorrow.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        by_service = {
            g["Keys"][0]: round(float(g["Metrics"]["UnblendedCost"]["Amount"]), 6)
            for g in r["ResultsByTime"][0]["Groups"]
        }
        mtd_total = round(sum(by_service.values()), 2)

        # 2) overall month-end forecast from Cost Explorer (actuals + predicted rest).
        #    GetCostForecast needs a future window; if today is the last day of the
        #    month there is nothing to forecast, so fall back to the MTD total.
        forecast_total = mtd_total
        if tomorrow < next_month:
            try:
                fr = ce.get_cost_forecast(
                    TimePeriod={"Start": tomorrow.isoformat(),
                                "End": next_month.isoformat()},
                    Metric="UNBLENDED_COST",
                    Granularity="MONTHLY",
                )
                rest_of_month = float(fr["Total"]["Amount"])
                forecast_total = round(mtd_total + rest_of_month, 2)
            except Exception:
                # If forecasting is unavailable (e.g. too little history), stay honest:
                # report the MTD total as the floor rather than inventing a number.
                forecast_total = mtd_total

        # 3) top 5 services by MTD spend, with a simple pro-rated month-end estimate
        days_elapsed = (today - first).days + 1
        days_in_month = (next_month - first).days
        scale = days_in_month / days_elapsed if days_elapsed else 1.0
        top = sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_services = [
            {
                "service": name,
                "month_to_date": round(amt, 2),
                "forecast_month_end": round(amt * scale, 2),  # pro-rated estimate
            }
            for name, amt in top
        ]

        return {
            "month_to_date_total": mtd_total,
            "forecast_month_end_total": forecast_total,
            "top_services": top_services,
        }


@tool
def list_buckets() -> list[str]:
    """List S3 bucket names in the account. READ-ONLY.

    Returns:
        A list of bucket names.
    """
    with _timed_tool("list_buckets"):
        s3 = boto3.client("s3", region_name=REGION)
        r = s3.list_buckets()
        return [b["Name"] for b in r.get("Buckets", [])]


@tool
def list_functions(region: str = REGION) -> list[dict]:
    """List Lambda functions (name and runtime) in a region. READ-ONLY.

    Args:
        region: AWS region to inspect. Defaults to us-east-1.

    Returns:
        A list of dicts like {"name": "...", "runtime": "python3.12"}.
    """
    with _timed_tool("list_functions"):
        lam = boto3.client("lambda", region_name=region)
        out: list[dict] = []
        paginator = lam.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                out.append({"name": fn["FunctionName"], "runtime": fn.get("Runtime", "n/a")})
        return out


@tool
def list_volumes(region: str = REGION) -> list[dict]:
    """List EBS volumes with size, type, and attachment state. READ-ONLY.

    An "available" volume is attached to nothing - a common source of wasted spend.

    Args:
        region: AWS region to inspect. Defaults to us-east-1.

    Returns:
        A list of dicts like
        {"id": "vol-...", "gib": 8, "type": "gp3", "state": "available"}.
    """
    with _timed_tool("list_volumes"):
        ec2 = boto3.client("ec2", region_name=region)
        r = ec2.describe_volumes()
        return [
            {
                "id": v["VolumeId"],
                "gib": v["Size"],
                "type": v["VolumeType"],
                "state": v["State"],  # "in-use" | "available" (available == unattached)
            }
            for v in r.get("Volumes", [])
        ]


@tool
def open_security_groups(region: str = REGION) -> list[dict]:
    """List security groups with an inbound rule open to 0.0.0.0/0. READ-ONLY.

    "Open to the whole internet" is a classic security finding. This only reads
    (describe) security groups; it never modifies them.

    Args:
        region: AWS region to inspect. Defaults to us-east-1.

    Returns:
        A list of dicts like {"id": "sg-...", "open_ports": [22, 3389]}.
    """
    with _timed_tool("open_security_groups"):
        ec2 = boto3.client("ec2", region_name=region)
        r = ec2.describe_security_groups()
        out: list[dict] = []
        for sg in r.get("SecurityGroups", []):
            open_ports: list[int] = []
            for perm in sg.get("IpPermissions", []):
                wide = any(rng.get("CidrIp") == "0.0.0.0/0" for rng in perm.get("IpRanges", []))
                if wide:
                    frm = perm.get("FromPort")
                    if frm is not None:
                        open_ports.append(frm)
                    else:
                        open_ports.append(-1)  # -1 == all ports
            if open_ports:
                out.append({"id": sg["GroupId"], "open_ports": sorted(set(open_ports))})
        return out


@tool
def mfa_findings() -> dict:
    """Check MFA coverage: root account and IAM users without MFA. READ-ONLY.

    Covers two classic IAM controls (CIS: MFA for the root account, and MFA for all
    IAM users). Only reads account summary and IAM user/MFA listings.

    Returns:
        A dict like:
          {"root_mfa_enabled": bool,
           "users_without_mfa": ["alice", "bob"],
           "users_checked": 10}
    """
    with _timed_tool("mfa_findings"):
        iam = boto3.client("iam")
        summary = iam.get_account_summary().get("SummaryMap", {})
        root_mfa = bool(summary.get("AccountMFAEnabled", 0))

        users_without: list[str] = []
        checked = 0
        paginator = iam.get_paginator("list_users")
        for page in paginator.paginate():
            for u in page.get("Users", []):
                checked += 1
                name = u["UserName"]
                devices = iam.list_mfa_devices(UserName=name).get("MFADevices", [])
                if not devices:
                    users_without.append(name)
        return {
            "root_mfa_enabled": root_mfa,
            "users_without_mfa": users_without,
            "users_checked": checked,
        }


@tool
def public_s3_buckets() -> list[dict]:
    """Find S3 buckets NOT fully protected by an account/bucket Public Access Block. READ-ONLY.

    A bucket whose Public Access Block does not block all four public vectors can be
    exposed to the internet. Only reads bucket listing and public-access-block config.

    Returns:
        A list of dicts like {"bucket": "name", "reason": "no public access block"}.
        An empty list means every bucket has a full public access block (good).
    """
    with _timed_tool("public_s3_buckets"):
        s3 = boto3.client("s3", region_name=REGION)
        findings: list[dict] = []
        for b in s3.list_buckets().get("Buckets", []):
            name = b["Name"]
            try:
                cfg = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
                fully_blocked = all([
                    cfg.get("BlockPublicAcls", False),
                    cfg.get("IgnorePublicAcls", False),
                    cfg.get("BlockPublicPolicy", False),
                    cfg.get("RestrictPublicBuckets", False),
                ])
                if not fully_blocked:
                    findings.append({"bucket": name, "reason": "public access block incomplete"})
            except s3.exceptions.from_code("NoSuchPublicAccessBlockConfiguration"):
                findings.append({"bucket": name, "reason": "no public access block"})
            except Exception as e:
                # Cross-region / access-denied on a single bucket: record honestly, don't guess.
                findings.append({"bucket": name, "reason": f"could not verify ({type(e).__name__})"})
        return findings


@tool
def guardduty_enabled(region: str = REGION) -> dict:
    """Check whether Amazon GuardDuty threat detection is enabled. READ-ONLY.

    GuardDuty is AWS's managed threat-detection service; no detector means no
    threat-detection "camera" is running. Only lists detectors.

    Args:
        region: AWS region to inspect. Defaults to us-east-1.

    Returns:
        A dict like {"enabled": false, "detector_count": 0}.
    """
    with _timed_tool("guardduty_enabled"):
        gd = boto3.client("guardduty", region_name=region)
        ids = gd.list_detectors().get("DetectorIds", [])
        return {"enabled": len(ids) > 0, "detector_count": len(ids)}
