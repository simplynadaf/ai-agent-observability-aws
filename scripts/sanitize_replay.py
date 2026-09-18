#!/usr/bin/env python3
"""Sanitize real identifiers in the committed replay/demo data before public release.

Only touches the CANNED replay data (sample_run.jsonl, sample_report.md). The LIVE path
stays fully real against AWS. Consistent 1:1 mapping so the demo stays coherent.
"""
import pathlib, re

ROOT = pathlib.Path("/home/ubuntu/traccia.ai/article1-observability/ai-agent-observability-aws")

# --- consistent replacements (real -> fake-but-realistic) ---
REPL = {
    # account id (only appears embedded in a bucket name)
    "175662053988": "111122223333",
    # IAM usernames
    "arman-nadaf": "dev-user",
    "ep2-n8n-bedrock-demo": "automation-demo",
    "github-action": "ci-deploy",
    "nightshift-test-user": "batch-runner",
    "naisha": "app-service",
    "Pratik": "analyst-a",
    "salman": "analyst-b",
    "sarvar-s": "platform-admin",
    "Terraform": "iac-runner",
    # NOTE: "server" is too generic to blanket-replace safely; handle via context below.
    # resource ids
    "i-019264cdc1b1eee07": "i-0abc123def4567890",
    "sg-055250cbcc6f3b37b": "sg-0aaaa1111bbbb2222",
    "sg-03f6adb963161c919": "sg-0cccc3333dddd4444",
    "sg-de63a5eb": "sg-0eeee5555",
    "vol-02b2d72609cf79295": "vol-0aaaa1111bbbb2222",
    "vol-00e39dd788a06ad4b": "vol-0cccc3333dddd4444",
    # S3 bucket names (personal/domain) -> generic demo names
    "dev.sarvarnadaf.com": "dev.example-app.com",
    "sarvarnadaf.com": "example-app.com",
    "dev.to-articles": "content-articles",
    "devops-mustkhim": "devops-team-a",
    "devops-sarvar": "devops-team-b",
    "devto-article-promotion": "article-promotion",
    "sarvars-youtube-videos": "media-assets",
    "infinite-gallery-site-111122223333": "demo-gallery-site-111122223333",  # after acct sub
    # lambda function names -> generic
    "bedrock-cost-agent": "cost-agent-fn",
    "genai-image-titan": "image-fn-a",
    "genai-image-generator": "image-fn-b",
    "terraform-lambda-hello": "hello-fn",
    "terraform-lambda-s3-processor": "s3-processor-fn",
    "Bedrock-OCR": "ocr-fn",
    "Demo-API": "demo-api-fn",
    # service identity email/owner kept generic-ish already (svc-investigator@sarvar-cloud) -> leave tenant, it's a made-up tenant
}

TARGETS = [
    ROOT / "ui" / "replay" / "sample_report.md",
    ROOT / "ui" / "replay" / "sample_run.jsonl",
]

def apply(text: str) -> str:
    # account id first so the bucket-name composite rule lands right
    text = text.replace("175662053988", REPL["175662053988"])
    for k, v in REPL.items():
        if k == "175662053988":
            continue
        text = text.replace(k, v)
    # handle the bare IAM user "server" only inside the MFA username list context
    # (avoid touching the word 'server' elsewhere). It appears as `server`, and in the
    # comma list ", `server`," -> ", `svc-runner`,"
    text = text.replace("`server`", "`svc-runner`")
    return text

for f in TARGETS:
    t = f.read_text()
    f.write_text(apply(t))
    print("sanitized", f.name)

print("done")
