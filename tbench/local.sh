#!/bin/sh
# purr on Terminal-Bench with a model on this machine (Ollama): free, one task at a time (one GPU).
# The model's own sampling settings come from config.toml (ornith-9b: what its published
# Terminal-Bench 2.1 score used). tbench/ollama_bridge.py lets the containers reach Ollama, and
# stops with the run. Then: tbench/publish.py <job folder> (local runs get their own rows).
#
#   tbench/local.sh                    the quick 20 on Terminal-Bench 2.1 with ornith-9b-128k
#   tbench/local.sh --one              every task once
#   PURR_LOCAL_MODEL=qwen3.6-iq2-64k tbench/local.sh    another Ollama model (its Ollama name)
#   tbench/local.sh -i git-leak-recovery                 tasks picked by hand
set -eu
cd "$(dirname "$0")/.."
export PURR_DATASET="${PURR_DATASET:-terminal-bench/terminal-bench-2-1}"
case "$PURR_DATASET" in */*) ORG="${PURR_DATASET%%/*}/" ;; *) ORG="" ;; esac
MODEL="${PURR_LOCAL_MODEL:-ornith-9b-128k}"
TASKS=""
if [ "${1:-}" = "--one" ]; then
    shift
else
    [ "${1:-}" = "--quick" ] && shift
    case " $* " in
        *" -i "*) ;;  # tasks picked by hand: just those
        *) TASKS=$(grep -v '^#' tbench/quick-tasks.txt | awk -v org="$ORG" 'NF {printf "-i %s%s ", org, $1}') ;;
    esac
fi
n=$#
while [ "$n" -gt 0 ]; do  # -i name -> -i org/name, like fair.sh
    a="$1"
    shift
    n=$((n - 1))
    if [ "$a" = "-i" ] && [ "$n" -gt 0 ]; then
        t="$1"
        shift
        n=$((n - 1))
        case "$t" in */*) ;; *) t="$ORG$t" ;; esac
        set -- "$@" -i "$t"
    else
        set -- "$@" "$a"
    fi
done
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "purr has uncommitted changes: commit first, so the results name the exact version" >&2
    [ "${PURR_ALLOW_DIRTY:-}" = 1 ] || exit 1
fi
# the exact name (ornith-9b isn't ornith-9b-128k), with or without Ollama's :latest
case "$(curl -fsS http://127.0.0.1:11434/api/tags 2>/dev/null)" in
    *"\"$MODEL\""*|*"\"$MODEL:latest\""*) ;;
    *) echo "Ollama doesn't have $MODEL (is it running? ollama list)" >&2; exit 1 ;;
esac
export PURR_MODEL="ollama/$MODEL"
# Ollama only listens on 127.0.0.1: the bridge lets the containers in (and nothing else), and both
# helpers stop with this process ($$, Harbor after the exec)
python3 tbench/ollama_bridge.py 11435 "$$" >/dev/null 2>&1 &
JOB_NAME="${PURR_JOB:-$(date +%Y-%m-%d__%H-%M-%S)}"
python3 tbench/clean_images.py "$$" "$HOME/.local/state/purr/tbench/$JOB_NAME" 1 >/dev/null 2>&1 &
# shellcheck disable=SC2086  # $TASKS is a list of -i options
exec tbench/run.sh --job-name "$JOB_NAME" -k 1 -n 1 $TASKS --max-retries 3 --retry-include RuntimeError --ak max_steps=500 "$@"
