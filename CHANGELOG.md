# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-04

### Added
- `planguard analyze` CLI: reads `terraform show -json` output and writes Markdown
  or JSON, with exit code 1 when risk >= `--fail-on`.
- Rules PG001–PG007: stateful destroy/replace, IAM wildcards, world-open
  ingress (AWS, GCP, Azure), mass delete, compute replace, protected tags,
  and attributes that force replacement.
- `.planguard.yml`: extra stateful/compute types, address ignore globs,
  protected tags, public ports, thresholds, and per-rule overrides.
- Composite GitHub Action with job summary, a sticky PR comment (created or
  updated through `gh api`), and outputs `risk`, `failed`, `headline`.
- Dogfooding `demo` workflow that runs a real Terraform plan from `examples/`.

[1.0.0]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.0.0
