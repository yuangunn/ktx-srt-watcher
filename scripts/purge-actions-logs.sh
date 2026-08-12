#!/usr/bin/env bash
#
# Delete the log bodies of existing GitHub Actions runs.
#
# Why: this repo is public, and Actions logs on a public repo are readable by
# anyone without signing in. Until the run that redacted them, every poll
# printed the watch id — origin, destination and travel date in the clear —
# so the run history is a browsable record of when the house is empty.
# Redacting the code fixes new runs only; already-published logs stay up
# until they are deleted, and lowering the retention period does not remove
# them (GitHub applies retention to new objects only).
#
# Deletes LOGS, not runs. The PWA health card reads the run list
# (listRuns('watch.yml')), so deleting runs outright would blank it. Removing
# just the log body leaves the run — its status, timing and conclusion — intact.
#
# Usage:
#   gh auth status                       # must be logged in, repo scope
#   ./scripts/purge-actions-logs.sh      # resumable; safe to re-run
#
# It snapshots the run ids first, then works through them, recording each id it
# finishes. Interrupt it whenever — the next run picks up where it stopped.

set -uo pipefail

REPO="${REPO:-yuangunn/ktx-srt-watcher}"
WORK_DIR="${WORK_DIR:-.purge-actions-logs}"
IDS_FILE="$WORK_DIR/run-ids.txt"
DONE_FILE="$WORK_DIR/done.txt"

# 4,500/hr, comfortably under the 5,000/hr REST cap so the job never stalls on
# the primary limit. Secondary (anti-burst) limits are handled by the retry.
SLEEP_BETWEEN="${SLEEP_BETWEEN:-0.8}"
BACKOFF_SEC="${BACKOFF_SEC:-60}"

command -v gh >/dev/null || { echo "gh CLI not found: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not authenticated — run: gh auth login" >&2; exit 1; }

mkdir -p "$WORK_DIR"
touch "$DONE_FILE"

if [[ ! -s "$IDS_FILE" ]]; then
  echo "실행 목록을 가져오는 중… (2만 건 이상이면 몇 분 걸립니다)"
  # Snapshot once. Runs created after this point were produced by the redacted
  # code and have nothing to hide, so there is no need to chase a moving list.
  gh api --paginate "/repos/$REPO/actions/runs?per_page=100" \
    --jq '.workflow_runs[].id' > "$IDS_FILE" || {
      echo "실행 목록 조회 실패" >&2; exit 1; }
fi

total=$(wc -l < "$IDS_FILE" | tr -d ' ')
echo "대상 $total건, 완료 $(wc -l < "$DONE_FILE" | tr -d ' ')건부터 이어서 진행합니다."

# Load the done list into memory once. Grepping the file per id would be
# quadratic — at 24k runs that is ~300M line comparisons of pure overhead.
declare -A DONE=()
while read -r d; do [[ -n "$d" ]] && DONE["$d"]=1; done < "$DONE_FILE"

deleted=0; missing=0; failed=0; n=0
while read -r id; do
  [[ -z "$id" ]] && continue
  n=$((n + 1))
  [[ -n "${DONE[$id]:-}" ]] && continue

  for attempt in 1 2 3; do
    # -i prints the status line; taking it from head -1 works whether or not
    # the response carries a body (204 does not).
    code=$(gh api -X DELETE "/repos/$REPO/actions/runs/$id/logs" -i 2>&1 \
             | head -1 | awk '{print $2}')
    case "$code" in
      204|202) deleted=$((deleted + 1)); break ;;
      # 404 = no log body (already deleted, or aged past retention). Done either
      # way; recording it stops a re-run from asking again.
      404|410)  missing=$((missing + 1)); break ;;
      403|429) echo "  rate limit — ${BACKOFF_SEC}s 대기 (시도 $attempt)"; sleep "$BACKOFF_SEC" ;;
      *) if [[ $attempt -eq 3 ]]; then
           failed=$((failed + 1)); echo "  실패 run=$id http=${code:-?}" >&2
         else sleep 5; fi ;;
    esac
  done

  echo "$id" >> "$DONE_FILE"
  if (( n % 200 == 0 )); then
    echo "[$n/$total] 삭제 $deleted · 이미없음 $missing · 실패 $failed"
  fi
  sleep "$SLEEP_BETWEEN"
done < "$IDS_FILE"

echo
echo "완료: 삭제 $deleted · 이미없음 $missing · 실패 $failed (총 $total)"
[[ $failed -gt 0 ]] && echo "실패분은 다시 실행하면 재시도합니다."
exit 0
