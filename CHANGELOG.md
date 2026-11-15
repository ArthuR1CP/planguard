# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Kubernetes: when the release namespace is known, an object without `metadata.namespace` is
  paired with the same object in that namespace, so adding an explicit `namespace:` to a
  template is no longer a delete + create (and a false CRITICAL on PVCs and StatefulSets).
  Helm mode in the action always passes `helm-namespace`. For manifests, use the new
  `planguard k8s --namespace` option or `k8s-namespace` input; without it, pairing is
  unchanged. Displayed addresses are unchanged. Moving an object to another namespace is
  still a delete + create.
- Helm hooks (`helm.sh/hook` on both the old and the new object) no longer trigger PG102
  when their immutable fields change (e.g. a new image in a migration Job), since Helm
  recreates them on every release. Plain Jobs still do.
- An object removed from the chart with `helm.sh/resource-policy: keep`, or a removed hook,
  is classified as `forget` instead of `delete`: Helm leaves it in the cluster. PG101 reports
  it as a LOW note, and it no longer counts toward PG004.

## [1.3.1] - 2026-11-09

### Changed
- Action metadata renamed to "planguard - Terraform & Helm risk check" with a shorter
  description, so it can be listed on GitHub Marketplace. `uses: ArthuR1CP/planguard@v1`
  is unchanged.

## [1.3.0] - 2026-11-08

### Changed
- Replacements with `create_before_destroy` are now recognised. PG005 (`compute-replace`)
  drops to LOW for them, since the new instance is up before the old one goes away.
  PG001 stays CRITICAL and says why: the new database or bucket still starts empty.
- The change list shows `replace (create first)`, and the JSON report has a
  `create_before_destroy` field on each change and on PG001/PG005 findings.

## [1.2.0] - 2026-11-05

### Added
- **Kubernetes / Helm support (beta).** `planguard k8s BEFORE AFTER` compares two sets of
  rendered manifests (`helm template`, `kustomize build` or plain YAML, file or directory).
- Rules PG101-PG106: stateful deletes (PVC, PV, StatefulSet, Namespace, CRD), immutable
  field changes, new public exposure (LoadBalancer, NodePort, Ingress hosts; internal load
  balancer annotations respected), RBAC wildcards / cluster-admin, capacity drops and
  privileged pod specs. PG004 (mass delete) and PG006 (protected) apply to Kubernetes too.
- GitHub Action: manifest mode (`k8s-before` / `k8s-after`) and Helm mode (`helm-chart`),
  which renders the chart on the PR base and head and compares them.
- `comment-id` input, so several planguard steps can each keep their own sticky comment.
- Config: `stateful_kinds`; `planguard/protected` label/annotation marks protected objects.

### Changed
- `plan-json` is no longer required; set exactly one mode.
- PyYAML is now used by the core (manifest parsing) as well as for config. It is still the
  only dependency.
- Project description: planguard now covers infrastructure changes in general, not only Terraform.

## [1.1.0] - 2026-11-04

### Added
- `--format text`: a compact, colored terminal view (Terraform-style `+ ~ -/+ -`
  symbols, aligned columns, PASS/FAIL line). It is the default when stdout is a
  terminal; piped output stays Markdown, so scripts and the action are unaffected.
- `--no-color`, plus support for the `NO_COLOR` and `FORCE_COLOR` environment variables.

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

[1.3.1]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.3.1
[1.3.0]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.3.0
[1.2.0]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.2.0
[1.1.0]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.1.0
[1.0.0]: https://github.com/ArthuR1CP/planguard/releases/tag/v1.0.0
