# Tests

## Smoke tests — `test_smoke.py`

Transpile small snippets and assert on the emitted C. Fast, no compiler needed.

## Differential tests — `test_differential.py` (+ `run_differential.sh`)

Cases live under `cases/<category>/<name>.f` (e.g. `cases/arrays/rank2.f`); the
harness discovers them recursively, so adding a category is just a new
subdirectory. Each `.f` file is a complete, self-contained Fortran **program**
that does some computation and **prints** the results. Every case is built and
run two ways, and the two stdout streams must be **byte-for-byte identical**:

1. **Fortran:** `gfortran -O0 case.f` → run.
2. **C:** transpile the whole file with fort2c — the `program` block becomes
   `int main`, its `print`/`write` statements become `printf` — then `gcc -O0`
   the generated C into a standalone executable → run.

Identical output is a clean pass/fail signal for "the C agrees with gfortran
bit-for-bit at -O0", the property fort2c exists to guarantee.

```bash
pytest tests/test_differential.py         # via pytest
tests/run_differential.sh                 # standalone, no pytest
tests/run_differential.sh arrays/rank2.f  # a single case
```

### Adding a case

Drop a fixed-form `.f` file in the appropriate `cases/<category>/`
subdirectory (make a new one if no category fits). Notes:

- Do the computation and the printing in the `program` block (or in
  subroutines/functions it calls). A `print` inside a **subroutine/function** is
  treated as a diagnostic and stripped, so keep program output in the program.
- Print with full precision (`es24.16` for doubles) so low mantissa bits are
  actually compared. Output formats are limited to what reproduces gfortran
  exactly: `I`, `F`, `ES`, `nX`, string literals and `/`.

The support header the generated C includes lives in `runtime/fort2c_runtime.h`.

## Probe cases — `cases/probes/` (`test_known_gap`)

A place to pin down a **known fort2c gap** as a failing case: a real program
where the C diverges from gfortran (a wrong number, code that does not compile,
or an `Unsupported` error). Probes are marked `xfail`, so the suite stays green
while the gap stays visible; each file's `PROBE:` comment becomes the xfail
reason. When a gap is fixed the probe turns `XPASS` — move it into the matching
`cases/<category>/` directory as a regression test. `run_differential.sh` skips
`probes/` unless a probe is named explicitly.

The directory currently holds one open gap — `big_exponent_format` (a 3-digit
decimal exponent: Fortran's `ES` drops the `E`, but C's `%E` keeps it, and
`printf` cannot express the difference). Every other probe has been fixed and
graduated into `cases/`. Round one — single-precision literal arithmetic,
`REAL*4` rounding, `INTEGER*8` overflow, `LOGICAL`, whole-array expressions, and
DO-loop trip count. Round two — integer `abs`, `modulo`, `select case`, computed
`goto`, array constructors, and whole-array / implied-do output lists. Round
three — negative integer power, integer `sign`, whole multi-dimensional-array
reductions, array-section arguments, statement functions, and runtime-bound
implied-do output. Round four — bit intrinsics, hyperbolic / `log10`,
`floor`/`ceiling`, `merge`/`dim`, the `where` construct, and `associate`. Round
five — `product`/`dot_product`, `count`/`all`/`any`, `maxloc`/`minloc`, numeric
inquiry (`epsilon`/`huge`/`tiny`/`bit_size`/`kind`), and the `forall` construct.
Round six — `save` / implicit-save, DATA implied-do, `dprod`, `ibits`/`ishftc`/
`mvbits`, and `transpose`. Round seven — fixed-length `character` scalars
(assignment/padding, `//` concatenation, substrings, comparison, `len`/
`len_trim`, `A`/`Aw` output). Round eight — character dummy arguments
(hidden-length ABI, assumed length `*(*)`), `trim`/`index`/`ichar`/`iachar`/
`char`/`achar`/`adjustl`/`adjustr`, and typed `function` result prefixes.
Round nine — array-valued intrinsics in assignments (`matmul`, `reshape`,
`cshift`, and `sum`/`product`/`maxval`/`minval` along a `dim`).
