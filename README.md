# fort2c

A **deterministic Fortran → C transpiler**. `fort2c` parses Fortran with
[fparser2](https://github.com/stfc/fparser) and emits C that follows the
gfortran ABI — lowercase symbol names with a trailing underscore, all arguments
passed by pointer, column-major array indexing.

Because it preserves the exact structure of every expression (it never
reassociates `a + b + c`, never turns `x**2` into `pow()`), the generated C
agrees with gfortran **bit-for-bit at `-O0`**. That bit-exactness is the whole
point: it makes a differential test (run the same inputs through the C and the
original Fortran, compare) a clean pass/fail signal, so a translation can be
*proven* correct rather than eyeballed.

`fort2c` grew out of porting the [fmm2d](https://github.com/flatironinstitute/fmm2d)
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
fort2c path/to/foo.f                 # print foo.c to stdout
fort2c path/to/foo.f --header        # print foo.h to stdout
fort2c path/to/foo.f -o out/         # write out/foo.c and out/foo.h
fort2c path/to/foo.f --only a,b      # emit only routines a and b
python -m fort2c path/to/foo.f       # same, without the console script
```

### Library

```python
import fort2c

c_src  = fort2c.generate_c("foo.f")              # -> str
h_src  = fort2c.generate_h("foo.f")              # -> str
c, h   = fort2c.transpile_file("foo.f", "out/")  # write both, return paths
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
fort2c foo.f --header --runtime-header my_runtime.h --guard-prefix MYLIB_
```

## What it handles

Scalar/array dummies and `intent`; `parameter`, `data` (scalar, whole-array,
subscripted via designated initializers, and implied-do); `dimension`
statements; `save` (and the implicit save of an initialized local, emitted as a
`static`); `allocatable` (bounds captured from `ALLOCATE`, multi-target,
`stat=`); C99 VLAs for automatic arrays; explicit, assumed-shape `(:)`,
assumed-size `(lo:*)` and arbitrary-lower-bound arrays via `FA2`/`FA3` or a
general column-major offset.

Program units: subroutines, functions, statement functions (`f(x) = expr`,
inlined at each use), and a `program` block (emitted as `int main`). A `print` /
`write(*|6, '(…)')` in the main program is translated to `printf` — output that
matches gfortran byte-for-byte for the `I`, `F`, `ES`, `nX`, string-literal and
`/` edit descriptors; whole arrays and implied-do lists in the output list are
expanded to their elements (a runtime-bound implied-do becomes a print loop).
The same statements inside a subroutine are treated as diagnostics and stripped
(the fmm2d convention).

Types: `integer`/`integer*8`, `real`/`real*4`/`real*8`/`double precision`,
`complex*16`, and `logical`, with the right C kind for each (single vs double
real, 32- vs 64-bit integer) and the matching numeric promotion — so single-
precision and 64-bit-integer arithmetic round and overflow exactly as gfortran
does. Fixed-length `character*N` scalars: assignment (blank-padded / truncated),
concatenation (`//`), substrings, blank-padded comparison, the intrinsics
`len`/`len_trim`/`trim`/`index`/`ichar`/`iachar`/`char`/`achar`/`adjustl`/
`adjustr`, `A`/`Aw` output, and character dummy arguments (the gfortran hidden
length argument, including assumed length `*(*)`) — emitted as `char[N]` with
small `fmm_str*` runtime helpers.

Control flow: `do` / `do while` / labeled `do`, `if`/`else if`/`else`,
`select case` (single values, value lists, ranges, default), `associate`
(scalar aliases and once-evaluated expressions), `forall` (single-index),
single-line, arithmetic and computed `goto`/labels, `exit`/`cycle`, `return`. A
counted `do` fixes its trip count at entry (Fortran semantics), so modifying the
bound in the body does not change the iteration count.

Expressions: full operator set with operation order preserved (arithmetic,
relational, and logical `.and.`/`.or.`/`.not.`/`.eqv.`); `complex*16` arithmetic;
complex/signed/logical literals; array constructors `(/.../)` / `[...]`;
whole-array assignment and element-wise arithmetic (`a = b*2`, `a = b + c`) and
the `where` masked-assignment construct (with `elsewhere`); `**` (exact
exponentiation-by-squaring for integer exponents, matching libgfortran); a
precision-aware intrinsic set — elementary (`int`/`nint`/`dble`/`real`/`abs`/
`iabs`/`sqrt`/`log`/`log10`/`exp`/`sin`/`cos`/`tan`/`asin`/`acos`/`sinh`/`cosh`/
`tanh`/`atan`/`atan2`), `floor`/`ceiling`, `sign`/`isign`/`dim`/`mod`/`dmod`/
`modulo`/`merge`/`dprod`, bit operations (`iand`/`ior`/`ieor`/`not`/`ishft`/
`ishftc`/`ibset`/`ibclr`/`ibits`/`btest`, and the `mvbits` subroutine), complex
(`dcmplx`/`dimag`/`dconjg`), numeric inquiry (`epsilon`/`huge`/`tiny`/`bit_size`/
`kind`), `max`/`min`, emitting the `sqrtf`-style single-precision libm variant
when the argument is single — and the array operations `maxval`/`minval`/`sum`/
`product` (over a section, a whole array of any rank, or along a `dim` →
rank-1), `dot_product`, the mask reductions `count`/`all`/`any`, `maxloc`/
`minloc`, `transpose`, `matmul`, `reshape`, and `cshift`. Array sections pass as
actual arguments by sequence association (the address of the first element).

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
needed by any currently-targeted file): `read` and other I/O, list-directed and
`E`/`D`/`G` output formats, array-valued function calls in a whole-array
expression, character beyond fixed-length scalars (character arrays and
character-valued functions; substring-target assignment), `equivalence`,
`common`, derived types, and `free` on every early-return path for heap
allocations (they may leak; harmless for testing).
One known formatting gap: `ES` output of a value with a 3-digit decimal exponent
(|exp| >= 100) keeps the `E` (`1.79E+308`) whereas gfortran drops it
(`1.79+308`) — `printf` cannot express this. New gaps can be pinned down as
`xfail` probe cases under `tests/cases/probes/` (currently holding the
3-digit-exponent case); the rest have since been fixed.

## License

Apache 2.0 — see [LICENSE](LICENSE).
