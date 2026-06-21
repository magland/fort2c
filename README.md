# fortc

A **deterministic Fortran → C transpiler**. `fortc` parses Fortran with
[fparser2](https://github.com/stfc/fparser) and emits C that follows the
gfortran ABI — lowercase symbol names with a trailing underscore, all arguments
passed by pointer, column-major array indexing.

Because it preserves the exact structure of every expression (it never
reassociates `a + b + c`, never turns `x**2` into `pow()`), the generated C
agrees with gfortran **bit-for-bit at `-O0`**. That bit-exactness is the whole
point: it makes a differential test (run the same inputs through the C and the
original Fortran, compare) a clean pass/fail signal, so a translation can be
*proven* correct rather than eyeballed.

`fortc` grew out of porting the [fmm2d](https://github.com/flatironinstitute/fmm2d)
2D Fast Multipole Method library to C (to compile to WebAssembly). It reproduces
that library's hand translation — 36 files across the Laplace, Helmholtz,
biharmonic, Stokes and modified-biharmonic solvers — file-for-file, bit-exact.

## Install

```bash
pip install -e .          # from a checkout
# runtime dependency: fparser (installed automatically)
```

## Use

### Command line

```bash
fortc path/to/foo.f                 # print foo.c to stdout
fortc path/to/foo.f --header        # print foo.h to stdout
fortc path/to/foo.f -o out/         # write out/foo.c and out/foo.h
fortc path/to/foo.f --only a,b      # emit only routines a and b
python -m fortc path/to/foo.f       # same, without the console script
```

### Library

```python
import fortc

c_src  = fortc.generate_c("foo.f")              # -> str
h_src  = fortc.generate_h("foo.f")              # -> str
c, h   = fortc.transpile_file("foo.f", "out/")  # write both, return paths
```

## The support header

The generated C targets a tiny **support header** that supplies the ABI
helpers the emitted code uses:

```c
#define FNAME(x) x##_              /* or x##_c_ for differential testing */
typedef int32_t        fint;       /* Fortran default integer            */
typedef double _Complex fcomplex;  /* complex*16                         */
#define FA2(i,j,ld1)         (((j)-1)*(ld1) + ((i)-1))           /* col-major */
#define FA3(i,j,k,ld1,ld2)  ((((k)-1)*(ld2) + ((j)-1))*(ld1) + ((i)-1))
```

By default the generated header `#include`s `"fmm2d_c.h"` with an `FMM2D_`
include-guard prefix (the fmm2d convention). Both are configurable:

```bash
fortc foo.f --header --runtime-header my_runtime.h --guard-prefix MYLIB_
```

## What it handles

Scalar/array dummies and `intent`; `parameter`, `data` (scalar, whole-array,
and subscripted via designated initializers); `dimension` statements;
`allocatable` (bounds captured from `ALLOCATE`, multi-target, `stat=`); C99 VLAs
for automatic arrays; explicit, assumed-shape `(:)`, assumed-size `(lo:*)` and
arbitrary-lower-bound arrays via `FA2`/`FA3` or a general column-major offset.

Control flow: `do` / `do while` / labeled `do`, `if`/`else if`/`else`,
single-line and arithmetic `IF`, `goto`/labels, `exit`/`cycle`, `return`.

Expressions: full operator set with operation order preserved; `complex*16` /
`double complex` arithmetic; complex/signed literals; `**` (exact
exponentiation-by-squaring for integer exponents, matching libgfortran);
intrinsics including `int`/`nint`/`dble`/`abs`/`sqrt`/`log`/`exp`/`atan`/`sin`/
`cos`/`sign`/`dcmplx`/`dimag`/`dconjg`/`max`/`min`/`mod` and the
`maxval`/`minval`/`sum` reductions over array sections.

It also performs the bookkeeping a faithful port needs: case-insensitive
identifiers (gfortran lowercases every symbol), same-file call-argument casts
(Fortran passes arguments untyped, so a real array routinely lands on a
`complex*16` parameter), identifier mangling for C keyword / libm collisions
(a Fortran variable literally named `pow`), `extern` prototypes for cross-file
calls, and stripping of OpenMP directives, `prini`-style logging and
`cpu_time`/`second`/`omp_get_wtime` timing.

Anything outside that set raises `Unsupported` with the offending parse node,
so gaps are loud, never silent.

## Limitations

Tailored to the numerical-Fortran subset fmm2d uses. Not yet handled (none are
needed by any currently-targeted file): `character`/string I/O beyond stripped
`print`s, `equivalence`, `common`, `save`, derived types, and `free` on every
early-return path for heap allocations (they may leak; harmless for testing).

## License

Apache 2.0 — see [LICENSE](LICENSE).
