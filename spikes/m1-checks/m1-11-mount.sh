#!/usr/bin/env bash
# HOST: M1.11. Is a read-write host mount safe for the factory directory (visibility, atomic renames, no NUL-filled files)?
# Usage: spikes/m1-checks/m1-11-mount.sh <sandbox-name> [N=300]
# Uses the dev-team sandbox's existing read-write mount of this repo (it appears at the same absolute path inside
# the sandbox, see `app-path` in the launcher) with a throwaway directory .m1-11-probe/ that is removed at the end.
# The factory dir today lives in the VM ($HOME/work/factory), NOT on a host mount; this tests whether it could.
# If anything FAILs the fallback in docs/work-plan.md M1.11 applies: keep it sandbox-local and read it with `sbx exec`.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
need_sbx
name="${1:?sandbox name, e.g. bayto-dev}"; N="${2:-300}"
probe="$REPO/.m1-11-probe"
begin m1-11
say "Sandbox: $name. Mount under test: $REPO (host path = sandbox path). Messages per storm: $N."; say
cleanup() { rm -rf "$probe"; sx "$name" "rm -rf '$probe'" >/dev/null 2>&1; }
trap cleanup EXIT
cleanup; mkdir -p "$probe"
sx "$name" "test -d '$probe' && test -w '$probe'" >/dev/null && pass "sandbox sees the host-created directory and can write to it" || { fail "sandbox cannot see or write $probe; the repo is not mounted read-write at the host path"; finish; exit 1; }

# 1. visibility both ways
sx "$name" "echo from-sandbox > '$probe/s2h.txt'" >/dev/null
[ "$(cat "$probe/s2h.txt" 2>/dev/null)" = from-sandbox ] && pass "sandbox -> host: a file written in the sandbox is visible on the host as soon as sbx exec returns" || fail "sandbox -> host: file not visible immediately after exec returned"
echo from-host > "$probe/h2s.txt"
[ "$(sx "$name" "cat '$probe/h2s.txt'")" = from-host ] && pass "host -> sandbox: a host-written file is visible in the sandbox immediately" || fail "host -> sandbox: file not visible immediately"

# 2. sandbox writes N messages with tmp+rename while the host reads each one as it appears
say; say "Atomic rename storm: sandbox writes $N JSON files (tmp + mv), host reads them concurrently."
mkdir -p "$probe/out"
python3 - "$name" "$probe/out" "$N" > "$RESULTS/.m1-11-storm.txt" <<'PY' &
import json, os, subprocess, sys, time
name, out, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
writer = ("cd '%s' && for i in $(seq 1 %d); do "
          "printf '{\"i\":%%d,\"pad\":\"%%s\"}\\n' $i \"$(head -c 3000 /dev/zero | tr '\\0' x)\" > .tmp-$i && mv .tmp-$i msg-$i.json; done" % (out, n))
p = subprocess.Popen(["sbx", "exec", name, "bash", "-lc", writer])
seen, bad, nul, partial = set(), 0, 0, 0
deadline = time.time() + 300
while (p.poll() is None or len(seen) < n) and time.time() < deadline:
    for f in os.listdir(out):
        if not f.startswith("msg-") or f in seen:
            continue
        data = open(os.path.join(out, f), "rb").read()
        if b"\x00" in data: nul += 1
        try: json.loads(data)
        except Exception: partial += 1; bad += 1
        seen.add(f)
    time.sleep(0.01)
print(len(seen), bad, nul, partial)
PY
wait
read -r seen bad nul partial < "$RESULTS/.m1-11-storm.txt"; rm -f "$RESULTS/.m1-11-storm.txt"
[ "${seen:-0}" -eq "$N" ] && pass "host saw all $N renamed files" || fail "host saw only ${seen:-0}/$N files"
[ "${bad:-1}" -eq 0 ] && [ "${partial:-1}" -eq 0 ] && pass "no file was ever seen empty or partial (rename is atomic across the mount)" || fail "$bad file(s) were unreadable/partial when first seen"
[ "${nul:-1}" -eq 0 ] && pass "no NUL bytes in any file" || fail "$nul file(s) contained NUL bytes"

# 3. host writes N files with tmp + os.replace, sandbox validates all of them
say; say "Reverse direction: host writes $N files (tmp + rename), sandbox validates."
mkdir -p "$probe/in"
python3 - "$probe/in" "$N" <<'PY'
import os, sys
d, n = sys.argv[1], int(sys.argv[2])
for i in range(1, n + 1):
    t = os.path.join(d, ".tmp-%d" % i)
    with open(t, "w") as f: f.write('{"i":%d,"pad":"%s"}\n' % (i, "y" * 3000))
    os.replace(t, os.path.join(d, "msg-%d.json" % i))
PY
v="$(sx "$name" "cd '$probe/in' && ok=0; nul=0; for f in msg-*.json; do jq -e . \"\$f\" >/dev/null 2>&1 && ok=\$((ok+1)); [ \"\$(tr -d '\\000' < \"\$f\" | wc -c)\" = \"\$(wc -c < \"\$f\")\" ] || nul=\$((nul+1)); done; echo \"\$ok \$nul \$(ls .tmp-* 2>/dev/null | wc -l)\"" | tail -n1)"
read -r ok nul2 leftover <<<"$v"
[ "${ok:-0}" -eq "$N" ] && pass "sandbox parsed all $N host-written files" || fail "sandbox parsed only ${ok:-0}/$N host-written files"
[ "${nul2:-1}" -eq 0 ] && pass "no NUL bytes in host-written files as seen from the sandbox" || fail "$nul2 host-written file(s) show NULs in the sandbox"
[ "${leftover:-1}" -eq 0 ] && pass "no stale .tmp files after the renames" || fail "$leftover stale .tmp file(s) visible in the sandbox"

# 4. append while reading (events.log style)
say; say "Append while reading: sandbox appends 2000 lines, host reads the file repeatedly."
: > "$probe/events.log"
sbx exec "$name" bash -lc "for i in \$(seq 1 2000); do echo \"line \$i ok\" >> '$probe/events.log'; done" &
wp=$!; torn=0; nulreads=0; reads=0
while kill -0 "$wp" 2>/dev/null; do
  d="$(python3 -c "import sys; b=open(sys.argv[1],'rb').read(); print(int(b'\x00' in b), int(len(b)>0 and not b.endswith(b'\n')))" "$probe/events.log")"
  reads=$((reads+1)); read -r dn dt <<<"$d"; nulreads=$((nulreads+dn)); torn=$((torn+dt))
done; wait "$wp"
lines="$(grep -cE '^line [0-9]+ ok$' "$probe/events.log")"
[ "$lines" -eq 2000 ] && pass "host sees all 2000 appended lines intact" || fail "host sees $lines/2000 intact lines"
[ "$nulreads" -eq 0 ] && pass "no read ever returned NUL bytes ($reads reads)" || fail "$nulreads of $reads reads returned NUL bytes"
[ "$torn" -eq 0 ] && info "no read ever ended mid-line ($reads reads)" || info "$torn of $reads reads ended mid-line: a tailer must buffer to the last newline (normal for append, not a failure)"

say; say "Verdict guide: all PASS -> a host mount of the factory dir is viable. Any FAIL -> use the fallback (sandbox-local factory dir, read with sbx exec), which is already how dev-team works."
finish
