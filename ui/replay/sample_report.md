Here's the report based on the gathered information:

### Health Section
**Running EC2 Instances:**
- Instance ID: `i-0a1b2c3d4e5f60718`, Type: `t3.2xlarge`, CPU Utilization: `1.82%`

**EBS Volumes:**
- Volume ID: `vol-0aa11bb22cc33dd44`, Size: `150 GiB`, Type: `gp2`, State: `in-use`
- Volume ID: `vol-0ee55ff66aa77bb88`, Size: `50 GiB`, Type: `gp3`, State: `in-use`

**Lambda Functions:**
- Name: `orders-api`, Runtime: `python3.12`
- Name: `image-thumbnailer`, Runtime: `python3.13`
- Name: `demo-handler`, Runtime: `python3.10`
- Name: `hello-world`, Runtime: `python3.11`
- Name: `invoice-ocr`, Runtime: `python3.14`
- Name: `public-api`, Runtime: `python3.14`
- Name: `s3-processor`, Runtime: `python3.11`
- Name: `image-generator`, Runtime: `python3.13`

**S3 Buckets:**
- `example-app-assets`
- `example-articles`
- `example-devops-a`
- `example-devops-b`
- `example-promotion`
- `example-gallery-site-000011112222`
- `example-static-site`
- `example-media-videos`

There are no unattached (available) EBS volumes in the `us-east-1` region.

### Security Section
1. **Security Groups Open to the Whole Internet**:
   - Security groups open to the whole internet (0.0.0.0/0) with the following open ports:
     - `sg-0a1b2c3d4e5f60718` with port `22`
     - `sg-0c1d2e3f` with ports `-1` and `22`
     - `sg-0f1e2d3c4b5a69780` with port `3389`
   - Risk: These security groups allow inbound traffic from any IP address, which can expose the associated resources to potential attacks. Port `22` is commonly used for SSH, and port `3389` is used for RDP, both of which are high-risk if exposed to the internet.

2. **MFA Gaps**:
   - The root account has MFA enabled. However, the following IAM users lack MFA:
     - `alice`
     - `bob`
     - `ci-deployer`
     - `carol`
     - `test-user`
     - `dave`
     - `erin`
     - `frank`
     - `service-account`
     - `terraform`
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
