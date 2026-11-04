#!/usr/bin/env bash
# Create or update a single planguard comment on the current pull request.
#
# Usage: sticky_comment.sh <report.md>
# Env:   GH_TOKEN, GITHUB_REPOSITORY, GITHUB_EVENT_PATH (set by GitHub Actions)
#        PLANGUARD_MARKER (optional, defaults to the marker planguard writes)
set -euo pipefail

body_file="${1:?usage: sticky_comment.sh <report.md>}"
marker="${PLANGUARD_MARKER:-<!-- planguard:sticky-comment -->}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"

pr_number="$(jq -r '.pull_request.number // .issue.number // empty' "${GITHUB_EVENT_PATH:?}")"
if [[ -z "${pr_number}" ]]; then
  echo "planguard: not a pull request event; skipping PR comment."
  exit 0
fi

if ! grep -qF -- "${marker}" "${body_file}"; then
  # Make sure the comment can be found next time.
  printf '%s\n%s' "${marker}" "$(cat "${body_file}")" > "${body_file}.marked"
  body_file="${body_file}.marked"
fi

# Find the first existing comment carrying our marker (all pages).
comment_id="$(
  gh api --paginate "repos/${repo}/issues/${pr_number}/comments?per_page=100" \
    --jq ".[] | select(.body | contains(\"${marker}\")) | .id" | head -n 1
)"

if [[ -n "${comment_id}" ]]; then
  gh api --method PATCH "repos/${repo}/issues/comments/${comment_id}" \
    -F "body=@${body_file}" --silent
  echo "planguard: updated comment ${comment_id} on PR #${pr_number}."
else
  gh api --method POST "repos/${repo}/issues/${pr_number}/comments" \
    -F "body=@${body_file}" --silent
  echo "planguard: created comment on PR #${pr_number}."
fi
