# Deployment Runbook · AWS Fargate

How to deploy RWAiSE (mounted on a Gopnik wallet host) to **AWS Fargate** behind an Application Load Balancer, with **RDS Postgres**, **ElastiCache Redis**, **Secrets Manager**, **Bedrock**, and **CloudWatch Logs**. Production-grade.

The full AWS module map is in [`architecture/02-aws-modules.svg`](architecture/02-aws-modules.svg).

---

## 0 · Pre-flight checklist

Before you start, you need:

- [ ] AWS account with admin (or scoped) access
- [ ] AWS CLI v2 configured (`aws sts get-caller-identity` works)
- [ ] An existing Gopnik production deployment OR a dev one to copy from
- [ ] Coinbase Developer Platform API key + secret (legacy HMAC format)
- [ ] (optional) Bedrock model access requested + approved for `anthropic.claude-sonnet-4-6` in your region
- [ ] A domain name with Route 53 (or external DNS) pointing at your ALB

---

## 1 · Architecture overview

```
                            Internet
                               │
                       Route 53 (DNS)
                               │
                              CDN ── (optional) CloudFront + WAF
                               │
                  Application Load Balancer
                               │
       ┌───────────────────────┼───────────────────────┐
       │                       │                       │
  ECS Fargate task        Fargate task            Fargate task
  (gunicorn + flask)      (gunicorn + flask)      (gunicorn + flask)
       │                       │                       │
       └──────┬────────────────┴──────────────┬────────┘
              │                                │
       RDS Postgres 15                  ElastiCache Redis 6
   (multi-AZ, encrypted)              (cluster, 1 primary 1 replica)

  Secrets Manager:                     Bedrock Runtime:           Coinbase CDP:
   - COINBASE_API_KEY                   - claude-sonnet-4-6        - api.coinbase.com
   - COINBASE_API_SECRET                                            (HMAC auth)
   - DB_PASSWORD                       CloudWatch Logs:
   - REDIS_AUTH                         - /ecs/gopnik-web
   - SECRET_KEY                         - structured JSON
```

See [`architecture/02-aws-modules.svg`](architecture/02-aws-modules.svg) for the diagram-version.

---

## 2 · Bootstrap AWS infra (one-time)

### 2a · Networking

A VPC with at least two public + two private subnets in different AZs. If you don't have one, the simplest path is to use the AWS-managed default VPC, then create a security group:

```bash
aws ec2 create-security-group \
  --group-name rwaise-fargate \
  --description "RWAiSE Fargate tasks"

# Allow inbound 5000 from the ALB SG only
# Allow outbound 443 to anywhere (Coinbase, Bedrock)
# Allow outbound 5432 to the RDS SG
# Allow outbound 6379 to the Redis SG
```

### 2b · RDS Postgres

```bash
aws rds create-db-instance \
  --db-instance-identifier gopnik-prod \
  --db-instance-class db.t4g.medium \
  --engine postgres --engine-version 15.5 \
  --allocated-storage 50 --storage-encrypted \
  --master-username gopnik --master-user-password '<set-via-secrets-manager-after>' \
  --multi-az --backup-retention-period 7 \
  --vpc-security-group-ids sg-xxxxxxx
```

Wait for status `available`, then apply schema:

```bash
psql "postgres://gopnik:<password>@<endpoint>:5432/gopnik" \
  < deployment/sql/rwaise_migration.sql
```

### 2c · ElastiCache Redis

```bash
aws elasticache create-replication-group \
  --replication-group-id gopnik-prod \
  --replication-group-description "Gopnik Redis (incl. RWAiSE feature flags)" \
  --engine redis --engine-version 7.1 \
  --cache-node-type cache.t4g.micro \
  --num-cache-clusters 2 \
  --transit-encryption-enabled --auth-token '<random-32-char-string>'
```

### 2d · Secrets Manager

```bash
# Store Coinbase keys
aws secretsmanager create-secret \
  --name gopnik/coinbase_api_key \
  --secret-string "<your-api-key-id>"

aws secretsmanager create-secret \
  --name gopnik/coinbase_api_secret \
  --secret-string "<your-api-secret>"

# DB password
aws secretsmanager create-secret \
  --name gopnik/db_password \
  --secret-string "<the-password-you-used-for-rds>"

# Flask secret
aws secretsmanager create-secret \
  --name gopnik/flask_secret_key \
  --secret-string "$(openssl rand -hex 32)"
```

### 2e · Bedrock model access

In the AWS Console → Bedrock → Model access → request access to `anthropic.claude-sonnet-4-6`. Approval is usually instant for Anthropic models.

Verify:
```bash
aws bedrock list-foundation-models --region eu-north-1 \
  --query 'modelSummaries[?contains(modelId, `sonnet-4-6`)].modelId'
```

### 2f · IAM role for the Fargate task

The task role needs to call Bedrock + Secrets Manager:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:eu-north-1::foundation-model/anthropic.claude-sonnet-4-6*"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:eu-north-1:*:secret:gopnik/*"
    }
  ]
}
```

The execution role needs ECR pull + CloudWatch Logs (the standard `AmazonECSTaskExecutionRolePolicy` covers it).

---

## 3 · Build + push the Docker image

```bash
# Authenticate to ECR
aws ecr get-login-password --region eu-north-1 | \
  docker login --username AWS --password-stdin <acct>.dkr.ecr.eu-north-1.amazonaws.com

# Build (this assumes a host Gopnik wallet checkout with rwaise mounted)
cd /path/to/gopnik_wallet
docker build -f Dockerfile.hardened -t gopnik:rwaise-v1.0.0 .

# Tag + push
docker tag gopnik:rwaise-v1.0.0 \
  <acct>.dkr.ecr.eu-north-1.amazonaws.com/gopnik:rwaise-v1.0.0
docker push <acct>.dkr.ecr.eu-north-1.amazonaws.com/gopnik:rwaise-v1.0.0
```

---

## 4 · Register the Fargate task definition

```bash
aws ecs register-task-definition \
  --cli-input-json file://deployment/ecs-task-definition.json
```

A sample task definition is in `deployment/ecs-task-definition.json` — the key parts:

```json
{
  "family": "gopnik-web",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::<acct>:role/ecsTaskExecutionRole",
  "taskRoleArn":      "arn:aws:iam::<acct>:role/gopnik-task-role",
  "containerDefinitions": [{
    "name": "web",
    "image": "<acct>.dkr.ecr.eu-north-1.amazonaws.com/gopnik:rwaise-v1.0.0",
    "portMappings": [{"containerPort": 5000, "protocol": "tcp"}],
    "essential": true,
    "environment": [
      { "name": "RWAISE_ENABLED",                "value": "1" },
      { "name": "RWAISE_FEATURE_BEDROCK_AGENT",  "value": "1" },
      { "name": "RWAISE_FEATURE_X402",           "value": "1" },
      { "name": "AWS_REGION",                    "value": "eu-north-1" },
      { "name": "RWAISE_BEDROCK_MODEL_ID",       "value": "anthropic.claude-sonnet-4-6" },
      { "name": "XRPL_RPC_URL",                  "value": "https://xrplcluster.com" },
      { "name": "DATABASE_URL",                  "value": "postgresql://gopnik:_PASS_@gopnik-prod.xxxx.rds.amazonaws.com:5432/gopnik" },
      { "name": "REDIS_URL",                     "value": "rediss://default:_AUTH_@gopnik-prod.xxxx.cache.amazonaws.com:6379/0" },
      { "name": "RATELIMIT_STORAGE_URI",         "value": "rediss://default:_AUTH_@gopnik-prod.xxxx.cache.amazonaws.com:6379/1" }
    ],
    "secrets": [
      { "name": "COINBASE_API_KEY",
        "valueFrom": "arn:aws:secretsmanager:eu-north-1:<acct>:secret:gopnik/coinbase_api_key" },
      { "name": "COINBASE_API_SECRET",
        "valueFrom": "arn:aws:secretsmanager:eu-north-1:<acct>:secret:gopnik/coinbase_api_secret" },
      { "name": "SECRET_KEY",
        "valueFrom": "arn:aws:secretsmanager:eu-north-1:<acct>:secret:gopnik/flask_secret_key" }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group":         "/ecs/gopnik-web",
        "awslogs-region":        "eu-north-1",
        "awslogs-stream-prefix": "ecs"
      }
    }
  }]
}
```

Note: do **NOT** set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars when using a task role — boto3 picks up the IAM role credentials automatically. Setting both is a footgun.

---

## 5 · Create the ECS service

```bash
aws ecs create-service \
  --cluster prod \
  --service-name rwaise-web \
  --task-definition gopnik-web \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={
    subnets=[subnet-xxx,subnet-yyy],
    securityGroups=[sg-rwaise-fargate],
    assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:eu-north-1:<acct>:targetgroup/gopnik-web/xxx,containerName=web,containerPort=5000" \
  --health-check-grace-period-seconds 60
```

---

## 6 · Verify the deployment

```bash
# 1. Service is RUNNING with desiredCount tasks
aws ecs describe-services --cluster prod --services rwaise-web \
  --query 'services[0].{Running:runningCount,Desired:desiredCount,Status:status}'

# 2. Health check
curl -fsSLI https://wallet.gopnik.io/health | head -1
# → HTTP/2 200

# 3. RWAiSE plugin loaded — check container logs
aws logs tail /ecs/gopnik-web --since 5m --filter-pattern 'RWAiSE plugin loaded'
# → "RWAiSE plugin loaded · flags={...}"

# 4. Static URL serves correctly (NOT double-prefixed)
curl -fsSLI https://wallet.gopnik.io/rwaise/static/js/agent_chat.js | head -1
# → HTTP/2 200

# 5. Sign in, then hit the agent debug endpoint
curl -fsSL https://wallet.gopnik.io/rwaise/agent/api/debug \
  -b cookies.txt | jq '.verdict'
# → "LIVE — real Coinbase data flowing. XRP spot $2.8543 (coinbase-cdp)."
```

---

## 7 · Environment variables · complete reference

| Variable | Required | Where it goes | What it does |
|---|---|---|---|
| `RWAISE_ENABLED` | ✅ | env | Master switch — `1` to enable plugin |
| `RWAISE_FEATURE_BEDROCK_AGENT` | ✅ | env | `1` to enable AI chat blueprint |
| `RWAISE_FEATURE_*` (10 more) | ⚪ | env | Per-feature env-var floors; leave unset to let admins toggle |
| `RWAISE_FEATURE_LIVE_BROADCAST` | ⚪ | env | `0` for demo (mock tx hashes), `1` to broadcast real MPTokenIssuanceCreate |
| `COINBASE_API_KEY` | recommended | Secrets Manager | Coinbase Cloud API key id |
| `COINBASE_API_SECRET` | recommended | Secrets Manager | Coinbase Cloud API secret |
| `COINBASE_API_PASSPHRASE` | ⚪ | Secrets Manager | ONLY if you have an old Coinbase Pro key |
| `CDP_API_KEY_NAME` | ⚪ | Secrets Manager | New CDP JWT key (alternative to HMAC) |
| `CDP_API_KEY_PRIVATE_KEY` | ⚪ | Secrets Manager | EC P-256 PEM block |
| `AWS_REGION` | ✅ | env | e.g. `eu-north-1` |
| `RWAISE_BEDROCK_MODEL_ID` | ⚪ | env | Default `anthropic.claude-sonnet-4-6` |
| `XRPL_RPC_URL` | ✅ | env | `https://xrplcluster.com` for mainnet |
| `RWAISE_PLATFORM_ADDRESS` | ⚪ | env | XRPL address that receives platform fees |
| `RWAISE_AGENT_SESSION_CAP_USD` | ⚪ | env | Default `1.00` |
| `RWAISE_X402_FACILITATOR_URL` | ⚪ | env | Default `/api/v1/coindesk-proxy` |
| `RWAISE_COINDESK_MOCK` | ❌ | env | **Do NOT set** in production |
| `RWAISE_AGENT_MOCK` | ❌ | env | **Do NOT set** in production |
| `RWAISE_BEDROCK_MOCK` | ❌ | env | **Do NOT set** in production |
| `DATABASE_URL` | ✅ | env (DB pwd from Secrets Manager) | Postgres connection string |
| `REDIS_URL` | ✅ | env (auth from Secrets Manager) | Redis connection string |
| `SECRET_KEY` | ✅ | Secrets Manager | Flask session signing key (32+ random bytes) |
| `RATELIMIT_STORAGE_URI` | ✅ | env | Use Redis for multi-worker |

---

## 8 · Rolling out updates

```bash
# Build new image
docker build -t gopnik:rwaise-v1.1.0 .
docker tag gopnik:rwaise-v1.1.0 <acct>.dkr.ecr.eu-north-1.amazonaws.com/gopnik:rwaise-v1.1.0
docker push <acct>.dkr.ecr.eu-north-1.amazonaws.com/gopnik:rwaise-v1.1.0

# Register a new task definition revision (point at the new image)
aws ecs register-task-definition --cli-input-json file://deployment/ecs-task-definition.json

# Roll the service
aws ecs update-service --cluster prod --service rwaise-web \
  --task-definition gopnik-web --force-new-deployment

# Watch the rollout
aws ecs describe-services --cluster prod --services rwaise-web \
  --query 'services[0].deployments[*].{Status:status,Running:runningCount,Desired:desiredCount,Created:createdAt}'
```

ECS does a rolling deploy with `minimumHealthyPercent=100`, `maximumPercent=200` by default — zero downtime as long as you have ≥2 tasks.

---

## 9 · Rollback

```bash
# Find the previous good revision
aws ecs list-task-definitions --family-prefix gopnik-web --status ACTIVE --sort DESC

# Pin the service back to it
aws ecs update-service --cluster prod --service rwaise-web \
  --task-definition gopnik-web:42 \
  --force-new-deployment
```

The plugin's `RWAISE_ENABLED=0` is also a valid emergency rollback — just toggle the env var off in the task def, register a new revision, redeploy. The plugin disappears in seconds; the host wallet keeps running.

---

## 10 · Operational dashboards

Set up CloudWatch dashboards for:

- **5xx rate** on the ALB target group (alarm at `> 1%` for 5 minutes)
- **Task CPU + memory** (alarm at `> 80%` for 10 minutes → scale up)
- **RDS connections + CPU**
- **Redis evictions** (should be 0 — if non-zero you need a bigger node)
- **Bedrock invocation count + errors** (per-tenant cost insight)
- **Custom metric `rwaise.agent.cost_usd`** (sum per hour; alarm at `> $50/hour` to catch runaway sessions)

Sample CloudWatch insights query for agent activity:

```
fields @timestamp, @message
| filter @message like /POST \/rwaise\/agent\/api\/chat/
| stats count() by bin(5m)
```

---

## 11 · Common production gotchas

### Static files 404 after deploy

Forgot to bake them into the image. Either:
- Add `COPY gopnik/rwaise/static /app/gopnik/rwaise/static` to your Dockerfile, OR
- Serve from S3/CloudFront and point `static_url_path` at the CDN

### "boto3 is set up but Bedrock returns AccessDeniedException"

Model access wasn't approved in the region. Go to Bedrock → Model access → request `anthropic.claude-sonnet-4-6`.

### Tasks restart in a loop right after deploy

Almost always the SQL migration wasn't applied. SSH into a bastion and run:

```bash
psql "$DATABASE_URL" < deployment/sql/rwaise_migration.sql
```

### Coinbase calls succeed locally but fail in Fargate

Outbound to `api.coinbase.com:443` is blocked by your security group. Add an egress rule:

```bash
aws ec2 authorize-security-group-egress \
  --group-id sg-rwaise-fargate \
  --protocol tcp --port 443 --cidr 0.0.0.0/0
```

(or restrict to Coinbase's published IP ranges if you're paranoid)

---

## 12 · Cost estimate

Running RWAiSE alongside Gopnik on AWS, per month:

| Service | Spec | Monthly |
|---|---|---|
| ECS Fargate | 2 × (1 vCPU + 2 GB) 24/7 | ~$60 |
| RDS Postgres | db.t4g.medium multi-AZ | ~$110 |
| ElastiCache Redis | 2 × cache.t4g.micro | ~$25 |
| ALB | 1 × always-on | ~$22 |
| Secrets Manager | ~10 secrets | ~$5 |
| CloudWatch Logs | ~5 GB ingestion | ~$3 |
| Bedrock Claude Sonnet 4.6 | ~10K invocations × 2K tokens avg | ~$60 |
| Data transfer | ~50 GB egress | ~$5 |
| **Total** | | **~$290 / month** |

Coinbase API calls are free under the 10 RPS public limit (no key required for market data).

---

[← back to README](../README.md)
