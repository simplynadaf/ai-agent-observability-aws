Here is the synthesized report based on the findings from the specialists:

### Health Report
**Running EC2 Instances:**
- Instance ID: `i-019264cdc1b1eee07`, Type: `t3.2xlarge`, CPU Utilization: 73%

**Lambda Functions:**
- `bedrock-cost-agent` (python3.12)
- `genai-image-titan` (python3.13)
- `Demo` (python3.10)
- `terraform-lambda-hello` (python3.11)

**S3 Buckets:** 8 buckets in the account.

**EBS Volumes:**
- `vol-02b2d72609cf79295`, 150 GiB, `gp2`, in-use
- `vol-00e39dd788a06ad4b`, 50 GiB, `gp3`, in-use

**Notable Health Findings:**
- There are no unattached (available) EBS volumes.

### Security Report
**Security Groups (open to the internet):**
- Security Group `sg-055250cbcc6f3b37b` is open to 0.0.0.0/0 on port 22 (SSH).
- Security Group `sg-de63a5eb` is open to 0.0.0.0/0 on all ports.
- Security Group `sg-03f6adb963161c919` is open to 0.0.0.0/0 on port 3389 (RDP).

**IAM MFA:**
- Root account MFA: enabled (good).
- 10 IAM users have no MFA device (e.g. `server`, `sarvar-s`, `github-action`, `Terraform`).

**S3 Public Access:**
- All buckets are protected by a full public access block. No exposed buckets found.

**GuardDuty:**
- GuardDuty threat detection is not enabled (no detector in us-east-1).

### Cost Report
**Actual Month-to-Date Spend:** $1.73
**Forecasted Month-End Total:** $71.19 (Cost Explorer forecast)

**Top 5 Services (actual month-to-date -> estimated month-end):**
- **Claude Sonnet 4.5 (Amazon Bedrock Edition)**: $1.46 -> ~$2.74
- **Tax**: $0.27 -> ~$0.51
- **EC2 - Other**: $0.08 -> ~$0.16
- **Claude Haiku 4.5 (Amazon Bedrock Edition)**: $0.00 -> ~$0.00
- **Amazon Elastic Compute Cloud - Compute**: $0.00 -> ~$0.00

Per-service month-end figures are pro-rated estimates; the forecasted month-end total
is Cost Explorer's own forecast for the account.
