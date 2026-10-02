# What fair.sh and local.sh both do before a run, sourced (". tbench/common.sh") once PURR_DATASET
# is set and the task list is in the arguments: a sourced file works on the caller's own arguments,
# so the -i rewriting below changes what they pass on to run.sh.

# a package dataset (org/name, like 2.1) names its tasks org/task: mteb-retrieve -> terminal-bench/mteb-retrieve
case "$PURR_DATASET" in */*) ORG="${PURR_DATASET%%/*}/" ;; *) ORG="" ;; esac
n=$#
while [ "$n" -gt 0 ]; do
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
