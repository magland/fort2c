#!/usr/bin/env bash
#
# Standalone differential-test runner (no pytest required).
#
# For each tests/cases/**/*.f file: build it with gfortran and run it, then
# transpile the whole file with fort2c (program -> int main, print -> printf),
# compile that C with gcc -O0 into a standalone executable, run it, and compare
# stdout byte-for-byte.
#
# Usage:
#   tests/run_differential.sh                    # run every case
#   tests/run_differential.sh arrays/rank2.f     # run one (or more) case(s)
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASES_DIR="$HERE/cases"
RUNTIME_DIR="$HERE/runtime"
RUNTIME_HEADER="fort2c_runtime.h"

# The probes/ directory holds cases that document known gaps and are expected
# to fail (see tests/test_differential.py::test_known_gap); skip them unless a
# case is named explicitly.
if (($# > 0)); then
    CASES=("$@")
else
    CASES=()
    while IFS= read -r f; do
        CASES+=("${f#"$CASES_DIR/"}")
    done < <(find "$CASES_DIR" -path "$CASES_DIR/probes" -prune -o -name '*.f' -print | sort)
fi

pass=0
fail=0
for case in "${CASES[@]}"; do
    src="$CASES_DIR/$case"
    base="$(basename "${case%.f}")"
    work="$(mktemp -d)"

    # 1. pure-Fortran build
    gfortran -O0 "$src" -o "$work/fort.bin" 2> "$work/err" || {
        echo "FAIL  $case  (gfortran build)"; cat "$work/err"; ((fail++)); continue; }
    "$work/fort.bin" > "$work/fort.out"

    # 2. transpile + standalone C build
    fort2c "$src" -o "$work" --runtime-header "$RUNTIME_HEADER" > /dev/null 2> "$work/err" || {
        echo "FAIL  $case  (fort2c transpile)"; cat "$work/err"; ((fail++)); continue; }
    gcc -O0 -I "$work" -I "$RUNTIME_DIR" "$work/$base.c" -o "$work/c.bin" -lm 2> "$work/err" || {
        echo "FAIL  $case  (gcc compile)"; cat "$work/err"; ((fail++)); continue; }
    "$work/c.bin" > "$work/c.out"

    # 3. compare
    if diff -q "$work/fort.out" "$work/c.out" > /dev/null; then
        echo "ok    $case"
        ((pass++))
    else
        echo "FAIL  $case  (output differs)"
        diff "$work/fort.out" "$work/c.out" | head -20
        ((fail++))
    fi
    rm -rf "$work"
done

echo "-----"
echo "$pass passed, $fail failed"
((fail == 0))
