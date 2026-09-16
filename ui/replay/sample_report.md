Here's the report based on the gathered information:

### Health Section
**Running EC2 Instances:**
- Instance ID: `i-019264cdc1b1eee07`, Type: `t3.2xlarge`, CPU Utilization: `1.82%`

**EBS Volumes:**
- Volume ID: `vol-02b2d72609cf79295`, Size: `150 GiB`, Type: `gp2`, State: `in-use`
- Volume ID: `vol-00e39dd788a06ad4b`, Size: `50 GiB`, Type: `gp3`, State: `in-use`

**Lambda Functions:**
- Name: `bedrock-cost-agent`, Runtime: `python3.12`
- Name: `genai-image-titan`, Runtime: `python3.13`
- Name: `Demo`, Runtime: `python3.10`
- Name: `terraform-lambda-hello`, Runtime: `python3.11`
- Name: `Bedrock-OCR`, Runtime: `python3.14`
- Name: `Demo-API`, Runtime: `python3.14`
- Name: `terraform-lambda-s3-processor`, Runtime: `python3.11`
- Name: `genai-image-generator`, Runtime: `python3.13`

**S3 Buckets:**
- `dev.sarvarnadaf.com`
- `dev.to-articles`
- `devops-mustkhim`
- `devops-sarvar`
- `devto-article-promotion`
- `infinite-gallery-site-175662053988`
- `sarvarnadaf.com`
- `sarvars-youtube-videos`

There are no unattached (available) EBS volumes in the `us-east-1` region.

### Security Section
1. **Security Groups Open to the Whole Internet**:
   - Security groups open to the whole internet (0.0.0.0/0) with the following open ports:
     - `sg-055250cbcc6f3b37b` with port `22`
     - `sg-de63a5eb` with ports `-1` and `22`
     - `sg-03f6adb963161c919` with port `3389`
   - Risk: These security groups allow inbound traffic from any IP address, which can expose the associated resources to potential attacks. Port `22` is commonly used for SSH, and port `3389` is used for RDP, both of which are high-risk if exposed to the internet.

2. **MFA Gaps**:
   - The root account has MFA enabled. However, the following IAM users lack MFA:
     - `arman-nadaf`
     - `ep2-n8n-bedrock-demo`
     - `github-action`
     - `naisha`
     - `nightshift-test-user`
     - `Pratik`
     - `salman`
     - `sarvar-s`
     - `server`
     - `Terraform`
   - Risk: IAM users without MFA are more susceptible to unauthorized access, as they rely solely on passwords for authentication.

3. **S3 Buckets Not Fully Protected by a Public Access Block**:
   - All S3 buckets are fully protected by a public access block.
   - Risk: None. All S3 buckets are properly configured to prevent public access.

4. **GuardDuty Threat Detection**:
   - GuardDuty threat detection is not enabled.
   - Risk: Without GuardDuty, the account lacks automated threat detection, which can leave it vulnerable to undetected malicious activities.

### Cost Section
1. **Actual Month-to-Date Total:** $1.73
2. **Forecasted Month-End Total:** $71.19
3. **Top 5 Services by Actual Month-to-Date Spend:**
   - **Claude Sonnet 4.5 (Amazon Bedrock Edition):** $1.46
   - **Tax:** $0.27
   - **EC2 - Other:** $0.08
   - **Claude Haiku 4.5 (Amazon Bedrock Edition):** $0.00
   - **Amazon Elastic Compute Cloud - Compute:** $0.00
