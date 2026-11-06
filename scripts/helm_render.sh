#!/usr/bin/env bash
# Render a Helm chart twice: at the current checkout ("after") and at a base git ref
# ("before"), so planguard can compare them.
#
# Usage: helm_render.sh <out_dir>
# Env:   CHART      chart directory, relative to the repository root (required)
#        VALUES     values files relative to the repo root, comma or newline separated
#        RELEASE    release name (default: the chart directory name)
#        NAMESPACE  release namespace (default: default)
#        BASE_REF   git ref/sha to compare against; empty or all zeros = "nothing before"
#
# Writes <out_dir>/before.yaml and <out_dir>/after.yaml.
set -euo pipefail

out="${1:?usage: helm_render.sh <out_dir>}"
chart="${CHART:?CHART is required}"
release="${RELEASE:-$(basename "${chart}")}"
namespace="${NAMESPACE:-default}"
mkdir -p "${out}"

if ! command -v helm >/dev/null 2>&1; then
  echo "::error title=planguard::helm is not installed on this runner (add azure/setup-helm before planguard)"
  exit 2
fi

values=()
while IFS= read -r line; do
  line="$(printf '%s' "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -n "${line}" ]] && values+=("${line}")
done < <(printf '%s\n' "${VALUES:-}" | tr ',' '\n')

# render <repo_root> <output_file> <label> <strict>
render() {
  local root="$1" dest="$2" label="$3" strict="$4"
  local args=()
  if [[ ! -f "${root}/${chart}/Chart.yaml" ]]; then
    if [[ "${strict}" == "true" ]]; then
      echo "::error title=planguard::no Chart.yaml in ${chart}"
      exit 2
    fi
    echo "planguard: no chart at ${chart} in ${label}; treating it as empty"
    : > "${dest}"
    return
  fi
  for v in ${values[@]+"${values[@]}"}; do
    if [[ -f "${root}/${v}" ]]; then
      args+=(-f "${root}/${v}")
    elif [[ "${strict}" == "true" ]]; then
      echo "::error title=planguard::values file not found: ${v}"
      exit 2
    fi
  done
  if grep -q '^dependencies:' "${root}/${chart}/Chart.yaml"; then
    helm dependency build "${root}/${chart}" >/dev/null
  fi
  helm template "${release}" "${root}/${chart}" --namespace "${namespace}" \
    ${args[@]+"${args[@]}"} > "${dest}"
  echo "planguard: rendered ${chart} at ${label} ($(grep -c '^kind:' "${dest}" || true) objects)"
}

render "${PWD}" "${out}/after.yaml" "the current checkout" true

base="${BASE_REF:-}"
if [[ -z "${base}" || "${base}" =~ ^0+$ ]]; then
  echo "planguard: no base ref; every object counts as new"
  : > "${out}/before.yaml"
  exit 0
fi

git fetch --no-tags --depth=1 origin "${base}" >/dev/null 2>&1 || true
worktree="${out}/base-worktree"
rm -rf "${worktree}"
git worktree add --detach "${worktree}" "${base}" >/dev/null 2>&1
trap 'git worktree remove --force "${worktree}" >/dev/null 2>&1 || true' EXIT
render "${worktree}" "${out}/before.yaml" "${base}" false
