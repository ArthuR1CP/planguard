#!/usr/bin/env bash
# Write the planguard config as it is on the PR base commit, so a PR can't loosen its own
# checks by editing .planguard.yml.
#
# Usage: base_config.sh <out_dir>
# Env:   BASE_REF    PR base commit (required on pull request events)
#        CONFIG      config path relative to the repo root (default: .planguard.yml, then
#                    .planguard.yaml)
#        EVENT_NAME  github.event_name
#
# Always sets the step output `path` to a file. When the base has no config, that file is
# empty, so planguard uses the built-in defaults instead of finding the head's config.
set -euo pipefail

out="${1:?usage: base_config.sh <out_dir>}"
mkdir -p "${out}"
dest="${out}/base.planguard.yml"

case "${EVENT_NAME:-}" in
  pull_request | pull_request_target) ;;
  *)
    echo "::error title=planguard::config-from-base only works on pull_request events (got '${EVENT_NAME:-}')"
    exit 2
    ;;
esac

base="${BASE_REF:-}"
if [[ -z "${base}" ]]; then
  echo "::error title=planguard::config-from-base: no PR base commit in the event payload"
  exit 2
fi

if ! git cat-file -e "${base}^{commit}" 2>/dev/null; then
  if ! git fetch --no-tags --depth=1 origin "${base}"; then
    echo "::error title=planguard::config-from-base: could not fetch the PR base commit ${base}"
    exit 2
  fi
fi

if [[ -n "${CONFIG:-}" ]]; then
  candidates=("${CONFIG#./}")
else
  candidates=(.planguard.yml .planguard.yaml)
fi

: > "${dest}"
found=""
for path in "${candidates[@]}"; do
  if git cat-file -e "${base}:${path}" 2>/dev/null; then
    git show "${base}:${path}" > "${dest}"
    found="${path}"
    break
  fi
done

if [[ -n "${found}" ]]; then
  echo "planguard: using ${found} from the PR base (${base})"
  if [[ -f "${found}" ]] && ! cmp -s "${found}" "${dest}"; then
    echo "::warning title=planguard::${found} is changed in this PR; the base version is used. The change takes effect after merge."
  fi
else
  echo "planguard: no config on the PR base (${base}); using the built-in defaults"
  for path in "${candidates[@]}"; do
    if [[ -f "${path}" ]]; then
      echo "::warning title=planguard::${path} is added in this PR; it takes effect after merge."
    fi
  done
fi
echo "path=${dest}" >> "${GITHUB_OUTPUT}"
