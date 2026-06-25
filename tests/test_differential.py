"""Differential tests: gfortran vs. fort2c+gcc, output must be bit-identical.

Each ``tests/cases/**/*.f`` file is a self-contained Fortran *program* (it does
the computation and prints the results). For every case we build and run it two
ways and compare stdout byte-for-byte:

1. **Fortran:** ``gfortran -O0 case.f`` -> run.
2. **C:** transpile the whole file with fort2c (the ``program`` block becomes
   ``int main`` and its ``print``s become ``printf``), ``gcc -O0`` the
   generated C into a standalone executable -> run.

The whole point of fort2c is that the C agrees with gfortran bit-for-bit at
-O0, so identical output is a clean pass/fail signal.
"""

import os
import shutil
import subprocess

import pytest

import fort2c

HERE = os.path.dirname(os.path.abspath(__file__))
CASES_DIR = os.path.join(HERE, "cases")
RUNTIME_DIR = os.path.join(HERE, "runtime")
RUNTIME_HEADER = "fort2c_runtime.h"

GFORTRAN = shutil.which("gfortran")
GCC = shutil.which("gcc")

pytestmark = pytest.mark.skipif(
    not (GFORTRAN and GCC),
    reason="gfortran and gcc are required for the differential tests",
)


PROBES_SUBDIR = "probes"


def _all_f(under=None):
    base = CASES_DIR if under is None else os.path.join(CASES_DIR, under)
    if not os.path.isdir(base):
        return []
    found = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".f"):
                found.append(os.path.relpath(os.path.join(root, f), CASES_DIR))
    return sorted(found)


def _cases():
    """Every ``*.f`` under cases/ except the probes/ directory, as paths
    relative to CASES_DIR (e.g. ``arrays/rank2.f``). These must all pass."""
    skip = PROBES_SUBDIR + os.sep
    return [c for c in _all_f() if not c.startswith(skip)]


def _probes():
    """The probe cases under cases/probes/ - each documents a known fort2c gap
    and is expected to fail (see test_known_gap)."""
    return _all_f(PROBES_SUBDIR)


def _probe_reason(case):
    for line in open(os.path.join(CASES_DIR, case)):
        if "PROBE:" in line:
            return line.split("PROBE:", 1)[1].strip()
    return "known fort2c gap"


def _run(cmd, cwd, timeout=20):
    # timeout guards against a buggy translation that loops forever (the
    # output then differs from gfortran, so the case fails rather than hangs)
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError("command timed out: %s" % " ".join(cmd))
    if p.returncode != 0:
        raise AssertionError(
            "command failed: %s\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (" ".join(cmd), p.stdout, p.stderr)
        )
    return p.stdout


def _build_and_run(case, work):
    """Run the gfortran and the fort2c+gcc builds; return (fort_out, c_out)."""
    src = os.path.join(CASES_DIR, case)
    base = os.path.splitext(os.path.basename(case))[0]

    # --- 1. pure-Fortran build -------------------------------------------
    fort_bin = os.path.join(work, "fort.bin")
    _run([GFORTRAN, "-O0", src, "-o", fort_bin], work)
    fort_out = _run([fort_bin], work)

    # --- 2. transpile the whole file to C (program -> main) --------------
    fort2c.transpile_file(src, work, basename=base, runtime_header=RUNTIME_HEADER)

    # --- 3. C build: gcc the generated C into a standalone executable ----
    c_bin = os.path.join(work, "c.bin")
    _run([GCC, "-O0", "-I", work, "-I", RUNTIME_DIR,
          os.path.join(work, base + ".c"), "-o", c_bin, "-lm"], work)
    c_out = _run([c_bin], work)

    return fort_out, c_out


@pytest.mark.parametrize("case", _cases())
def test_differential(case, tmp_path):
    fort_out, c_out = _build_and_run(case, str(tmp_path))
    assert fort_out == c_out, (
        "output mismatch for %s\n--- gfortran ---\n%s\n--- fort2c+gcc ---\n%s"
        % (case, fort_out, c_out)
    )


_PROBE_CASES = _probes()


@pytest.mark.skipif(not _PROBE_CASES, reason="no open known-gap probes")
@pytest.mark.parametrize("case", _PROBE_CASES or [""])
def test_known_gap(case, tmp_path, request):
    """Probe cases under cases/probes/ that document a known fort2c gap. Marked
    xfail (non-strict): today they fail (a wrong number, a non-compiling
    translation, or an Unsupported); if one starts passing it shows up as XPASS,
    a signal that the gap was closed and the probe can graduate into the real
    suite. (There are currently none - every probe has been fixed.)"""
    request.node.add_marker(pytest.mark.xfail(reason=_probe_reason(case),
                                              strict=False))
    fort_out, c_out = _build_and_run(case, str(tmp_path))
    assert fort_out == c_out, (
        "output mismatch for %s\n--- gfortran ---\n%s\n--- fort2c+gcc ---\n%s"
        % (case, fort_out, c_out)
    )


def test_there_are_cases():
    assert _cases(), "no .f test cases found under tests/cases/"
