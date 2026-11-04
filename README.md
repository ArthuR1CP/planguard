# 🛡️ planguard

[![ci](https://github.com/ArthuR1CP/planguard/actions/workflows/ci.yml/badge.svg)](https://github.com/ArthuR1CP/planguard/actions/workflows/ci.yml)
[![demo](https://github.com/ArthuR1CP/planguard/actions/workflows/demo.yml/badge.svg)](https://github.com/ArthuR1CP/planguard/actions/workflows/demo.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**planguard reads your `terraform plan` and posts one clear PR comment when it would destroy a database, open SSH to the world, or grant `"*"` in IAM, before anyone clicks merge.**

## The problem

A one-line change like `storage_encrypted = true` looks harmless in a diff. Terraform's plan says `-/+ must be replaced`, buried on line 412 of a CI log nobody reads. The PR gets approved, and the production database is recreated empty.

planguard reads the plan JSON and turns it into a comment reviewers can't miss:

> ## 🛡️ planguard: 🔴 CRITICAL
>
> ### 🚨 1 database will be REPLACED
>
> **Plan:** ➕ 0 to add · ✏️ 1 to change · ♻️ 1 to replace · 🗑️ 0 to destroy
>
> | Risk | Resource | Rule | Why |
> |---|---|---|---|
> | 🔴 **CRITICAL** | `aws_db_instance.main` | PG001 stateful-destroy | Will be REPLACED (forces replacement: `storage_encrypted`) |
>
> <details><summary>All changes (2)</summary>
>
> | Action | Resource | Type | Risk |
> |---|---|---|---|
> | ♻️ replace | `aws_db_instance.main` | `aws_db_instance` | 🔴 CRITICAL |
> | ✏️ update | `aws_db_parameter_group.main` | `aws_db_parameter_group` |  |
>
> </details>
>
> <sub>❌ failing · fail-on: `high` · planguard v1.0.0</sub>

The comment is **sticky**: each new push updates the same comment instead of adding another one. The same report goes to the job summary, and the step fails when the risk is at or above your `fail-on` level.

## Quickstart (GitHub Actions)

```yaml
# .github/workflows/terraform.yml
on: pull_request
permissions: { contents: read, pull-requests: write }
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with: { terraform_wrapper: false }
      - run: terraform init && terraform plan -out=tfplan && terraform show -json tfplan > plan.json
      - uses: ArthuR1CP/planguard@v1
        with: { plan-json: plan.json, fail-on: high }
```

Set `terraform_wrapper: false`, because the wrapper adds extra text to stdout and the JSON stops being valid.

### Action inputs and outputs

| Input | Default | Description |
|---|---|---|
| `plan-json` | (required) | Path to `terraform show -json` output |
| `config` | `./.planguard.yml` if present | Config file path |
| `fail-on` | `high` (or config) | `low` \| `medium` \| `high` \| `critical` \| `none` |
| `comment` | `true` | Create/update the sticky PR comment |
| `github-token` | `${{ github.token }}` | Needs `pull-requests: write` |

Outputs: `risk`, `failed`, `headline`, `report-markdown`, `report-json`.

On PRs from forks the token is read-only. The comment step then logs a warning and doesn't fail; the job summary still shows the report.

## CLI

```bash
pip install git+https://github.com/ArthuR1CP/planguard@v1
terraform show -json tfplan > plan.json
planguard analyze plan.json                     # colored summary in a terminal, Markdown when piped
planguard analyze plan.json --format md         # force Markdown (e.g. to paste into a PR)
planguard analyze plan.json --format json       # machine-readable
planguard analyze plan.json --fail-on critical --config .planguard.yml
planguard analyze - < plan.json                 # stdin
planguard rules                                 # list rules
```

In a terminal you get a compact, colored view using Terraform's own `+ ~ -/+ -` symbols:

```text
planguard v1.0.0  risk: CRITICAL
🚨 1 database will be REPLACED

Plan: 0 to add, 1 to change, 1 to replace, 0 to destroy

Findings (1)
  CRITICAL  aws_db_instance.main
            Will be REPLACED (forces replacement: storage_encrypted)  [PG001 stateful-destroy]

Changes (2)
  -/+  aws_db_instance.main         aws_db_instance         CRITICAL
    ~  aws_db_parameter_group.main  aws_db_parameter_group

✗ FAIL  risk CRITICAL ≥ fail-on HIGH
```

Colors follow [`NO_COLOR`](https://no-color.org) / `FORCE_COLOR`, or pass `--no-color`.
Exit codes: `0` ok · `1` risk ≥ `fail-on` · `2` invalid input or config.
`--output report.md` and `--json-output report.json` write both formats in a single run.

## Rules

| ID | Name | Risk | Fires on |
|---|---|---|---|
| PG001 | `stateful-destroy` | 🔴 CRITICAL | Delete or replace of stateful resources: RDS/Aurora, DynamoDB, S3, EFS/EBS/FSx, ElastiCache, OpenSearch, Redshift, KMS keys, Cloud SQL, GCS, BigQuery, Spanner, Azure SQL/Postgres/MySQL/Cosmos, storage accounts, Key Vault and others |
| PG002 | `iam-wildcard` | 🟠 HIGH | Allow statements with `Action: "*"` / `"svc:*"` / `NotAction`, or an `AdministratorAccess` attachment. Downgraded to 🟡 MEDIUM when only `Resource: "*"` is present |
| PG003 | `sg-open-world` | 🟠 HIGH | Ingress from `0.0.0.0/0` or `::/0` on any port other than 80/443 (AWS SG / SG rules / VPC ingress rules, GCP firewalls, Azure NSG rules) |
| PG004 | `mass-delete` | 🟠 HIGH | More than `max_deletes` (default 5) resources destroyed or replaced in a single plan |
| PG005 | `compute-replace` | 🟡 MEDIUM | Replacement of instances, launch templates, ASGs, EKS/GKE/AKS clusters and node pools, VMs |
| PG006 | `protected-tag` | 🟡 MEDIUM | Change to a resource tagged `planguard:protected=true` (🟠 HIGH if destroyed or replaced) |
| PG007 | `forces-replacement` | 🟢 LOW | Lists the attributes forcing each replacement (from Terraform's `replace_paths`) |

The overall risk is the highest-risk finding. A plan with changes but no findings is LOW; an empty plan is NONE.
PG002 and PG003 report only grants or exposures **introduced** by the plan. An existing wildcard policy whose tags change is not reported again.

## Configuration

Add an optional `.planguard.yml` at the repo root. Every key is optional:

```yaml
fail_on: high                  # low | medium | high | critical | none
thresholds:
  max_deletes: 5               # PG004
stateful_types:                # added to the built-in list, globs allowed
  - aws_opensearch_domain
  - "aws_mq_*"
compute_types: [aws_batch_compute_environment]
ignore:                        # resource address globs, skipped entirely
  - "module.sandbox.*"
  - "aws_cloudwatch_log_group.tmp_*"
protected_tags:                # PG006; "*" matches any value
  planguard:protected: "true"
  criticality: "*"
public_ports: [80, 443]        # PG003 allow-list for 0.0.0.0/0
rules:                         # turn a rule off or change its level
  PG005: off
  PG002: critical
```

Unknown keys cause an error (exit 2), so typos don't get ignored silently. The CLI flag `--fail-on` (or the action input) overrides `fail_on`.

## How is this different from Checkov / tfsec / Trivy / Infracost?

| Tool | Answers |
|---|---|
| Checkov, tfsec, Trivy, KICS | *Is this configuration secure?* Static analysis of `.tf` files against hundreds of policies |
| Infracost | *How much will this cost?* |
| **planguard** | ***What does this plan destroy, and how bad is it?*** The blast radius of one specific change |

planguard looks at the **diff** (the plan), not the code. It knows that `storage_encrypted = true` on an existing RDS instance means *data loss*, even though the setting itself passes every security scanner. Use it **next to** your scanner, not instead of it. It has one dependency (PyYAML), runs in about a second, and needs no API key or account.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check . && ruff format --check .
```

The [`demo`](.github/workflows/demo.yml) workflow dogfoods the action. It runs a real `terraform plan` on [`examples/`](examples) (only the `null` and `local` providers, no cloud credentials needed) and runs `uses: ./` on the result.

## Roadmap

- [ ] OpenTofu: test against `tofu show -json` and document it (the format is compatible today)
- [ ] Pulumi: support `pulumi preview --json`
- [ ] Optional LLM summary: a plain-English "what this PR really does" paragraph (opt-in, bring your own key)
- [ ] `moved`/`removed` block awareness, so planned refactors don't trigger PG004
- [ ] SARIF output for GitHub code scanning
- [ ] PyPI release

## License

[MIT](LICENSE)
