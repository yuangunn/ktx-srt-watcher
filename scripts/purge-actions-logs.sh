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
# Ids that exhausted their retries. Resume works by offset, so every id has to
# be recorded as processed whatever its outcome — which means a failure would
# otherwise be skipped forever by the next run. They are collected here and
# replayed with --retry-failed.
FAILED_FILE="$WORK_DIR/failed.txt"

# 4,500/hr, comfortably under the 5,000/hr REST cap so the job never stalls on
# the primary limit. Secondary (anti-burst) limits are handled by the retry.
SLEEP_BETWEEN="${SLEEP_BETWEEN:-0.8}"
BACKOFF_SEC="${BACKOFF_SEC:-60}"
NET_RETRY_SEC="${NET_RETRY_SEC:-15}"

command -v gh >/dev/null || { echo "gh CLI not found: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not authenticated — run: gh auth login" >&2; exit 1; }

mkdir -p "$WORK_DIR"
touch "$DONE_FILE" "$FAILED_FILE"

# --retry-failed replays the ids that ran out of retries, using its own source
# and done files so the offset resume stays valid for each. Rerunnable: each
# invocation takes whatever has accumulated in failed.txt.
if [[ "${1:-}" == "--retry-failed" ]]; then
  RETRY_SRC="$WORK_DIR/retry.txt"
  if [[ ! -s "$RETRY_SRC" ]]; then
    if [[ ! -s "$FAILED_FILE" ]]; then
      echo "재시도할 실패 항목이 없습니다."
      exit 0
    fi
    sort -u "$FAILED_FILE" > "$RETRY_SRC"
    : > "$FAILED_FILE"
    : > "$WORK_DIR/retry-done.txt"
  fi
  IDS_FILE="$RETRY_SRC"
  DONE_FILE="$WORK_DIR/retry-done.txt"
  touch "$DONE_FILE"
  echo "재시도 모드: $(wc -l < "$IDS_FILE" | tr -d ' ')건"
fi

if [[ ! -s "$IDS_FILE" ]]; then
  echo "실행 목록을 가져오는 중… (2만 건 이상이면 몇 분 걸립니다)"
  # Snapshot once. Runs created after this point were produced by the redacted
  # code and have nothing to hide, so there is no need to chase a moving list.
  # Write to a temp file and move it into place only once it has content: a
  # half-finished or empty snapshot left at IDS_FILE would be silently reused
  # by the next run, which is how "nothing to do" gets mistaken for success.
  if ! gh api --paginate "/repos/$REPO/actions/runs?per_page=100" \
       --jq '.workflow_runs[].id' > "$IDS_FILE.tmp" 2> "$WORK_DIR/fetch-err.txt"; then
    echo "실행 목록 조회 실패:" >&2
    sed 's/^/  /' "$WORK_DIR/fetch-err.txt" >&2
    rm -f "$IDS_FILE.tmp"
    exit 1
  fi
  if [[ ! -s "$IDS_FILE.tmp" ]]; then
    echo "실행 목록이 비어 있습니다. gh는 성공했지만 ID를 하나도 못 받았습니다." >&2
    echo "직접 확인해 보세요:" >&2
    echo "  gh api '/repos/$REPO/actions/runs?per_page=1' --jq '.total_count'" >&2
    [[ -s "$WORK_DIR/fetch-err.txt" ]] && { echo "gh stderr:" >&2; sed 's/^/  /' "$WORK_DIR/fetch-err.txt" >&2; }
    rm -f "$IDS_FILE.tmp"
    exit 1
  fi
  mv "$IDS_FILE.tmp" "$IDS_FILE"
  echo "실행 $(wc -l < "$IDS_FILE" | tr -d ' ')건을 받았습니다."
fi

total=$(wc -l < "$IDS_FILE" | tr -d ' ')
done_n=$(wc -l < "$DONE_FILE" | tr -d ' ')
# Braces are load-bearing: bash on macOS treats the following Hangul byte as
# part of the variable name, so "$total건" reads as an undefined variable and
# set -u kills the script.
echo "대상 ${total}건, 완료 ${done_n}건부터 이어서 진행합니다."

if [[ "$done_n" -ge "$total" && "$total" -gt 0 ]]; then
  echo "이미 전부 처리했습니다. 다시 하려면 $WORK_DIR 를 지우세요."
  # Saying "all done" while failures are outstanding is how a partial purge
  # gets mistaken for a finished one.
  if [[ -s "$FAILED_FILE" ]]; then
    echo "다만 실패 $(wc -l < "$FAILED_FILE" | tr -d ' ')건이 아직 남아 있습니다. 재시도:"
    echo "  $0 --retry-failed"
  fi
  exit 0
fi

deleted=0; missing=0; failed=0; n=$done_n
# Resume by offset rather than by lookup: done.txt gains exactly one line per
# id, in ids-file order, and the ids file is snapshotted once — so the counts
# line up. A per-id grep would be quadratic (~300M comparisons at 24k), and an
# associative array is not an option: macOS ships bash 3.2, which has none.
# Process substitution keeps the loop in this shell so the counters survive it.
while read -r id; do
  [[ -z "$id" ]] && continue
  n=$((n + 1))

  for attempt in 1 2 3; do
    # -i prints the status line; taking it from head -1 works whether or not
    # the response carries a body (204 does not). But gh also reports transport
    # failures here — `Delete "https://…": dial tcp …` — which have no status
    # line at all, so only treat the first line as one when it looks like one.
    # Reading field 2 unconditionally turned a network blip into http="https://…".
    first=$(gh api -X DELETE "/repos/$REPO/actions/runs/$id/logs" -i 2>&1 | head -1)
    case "$first" in
      HTTP/*) code=$(awk '{print $2}' <<< "$first") ;;
      *)      code="" ;;
    esac
    case "$code" in
      204|202) deleted=$((deleted + 1)); break ;;
      # 404 = no log body (already deleted, or aged past retention). Done either
      # way; recording it stops a re-run from asking again.
      404|410)  missing=$((missing + 1)); break ;;
      403|429) echo "  rate limit — ${BACKOFF_SEC}s 대기 (시도 $attempt)"; sleep "$BACKOFF_SEC" ;;
      "") # No status line: connectivity, DNS, TLS. These arrive in bursts, so
          # back off properly rather than retrying into the same dead network.
          if [[ $attempt -eq 3 ]]; then
            failed=$((failed + 1)); echo "$id" >> "$FAILED_FILE"
            echo "  실패 run=$id — $first" >&2
          else sleep "$NET_RETRY_SEC"; fi ;;
      *) if [[ $attempt -eq 3 ]]; then
           failed=$((failed + 1)); echo "$id" >> "$FAILED_FILE"
           echo "  실패 run=$id http=$code" >&2
         else sleep 5; fi ;;
    esac
  done

  echo "$id" >> "$DONE_FILE"
  if (( n % 200 == 0 )); then
    echo "[$n/$total] 삭제 $deleted · 이미없음 $missing · 실패 $failed"
  fi
  sleep "$SLEEP_BETWEEN"
done < <(tail -n +$((done_n + 1)) "$IDS_FILE")

echo
echo "완료: 삭제 $deleted · 이미없음 $missing · 실패 $failed (총 $total)"
if [[ -s "$FAILED_FILE" ]]; then
  # Plain re-running will NOT pick these up — resume is by offset, so they are
  # already counted as processed. They have to be replayed explicitly.
  echo "실패 $(wc -l < "$FAILED_FILE" | tr -d ' ')건이 남아 있습니다. 재시도:"
  echo "  $0 --retry-failed"
fi
exit 0
