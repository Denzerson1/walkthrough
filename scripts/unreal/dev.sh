#!/usr/bin/env bash
# Try a snippet in the open editor (run.py dev): dev.sh < snippet.py
# Prints what it printed. Only for working on the scripts here.
D="$(cd "$(dirname "$0")/../.." && pwd)/data/projects/flat-02/unreal"
rm -f "$D/cmd.out"; cat > "$D/cmd.tmp"; mv "$D/cmd.tmp" "$D/cmd.py"
for i in $(seq 1 ${DEV_WAIT:-240}); do [ -f "$D/cmd.out" ] && grep -q "#done" "$D/cmd.out" && break; sleep 0.5; done
cat "$D/cmd.out" 2>/dev/null || echo "(no answer)"
