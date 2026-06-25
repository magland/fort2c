# Probe cases (known gaps)

Drop a fixed-form `.f` program here to document a fort2c gap that is **not yet
fixed**: a case where the C translation diverges from gfortran (a wrong number,
code that does not compile, or an `Unsupported` error). Start one line with
`PROBE:` describing the gap — `test_known_gap` uses it as the `xfail` reason.

These run as non-strict `xfail`, so the suite stays green while the gap stays
visible. When a gap is fixed the probe turns `XPASS`; move it into the
appropriate `cases/<category>/` directory to keep it as a regression test.

This directory is currently empty — every probe written so far has been fixed
and graduated (logical, integer*8, single precision, whole-array expressions,
DO-loop trip count).
