"""Smoke tests: transpile small Fortran snippets and check the emitted C."""

import textwrap

import fortc


def _write(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_integer_function_with_goto(tmp_path):
    src = _write(tmp_path, "next.f", """
          function next235(base)
          implicit none
          integer :: next235, numdiv
          double precision :: base
          next235 = 2 * int(base/2d0+.9999d0)
          if (next235.le.0) next235 = 2
    100   numdiv = next235
          if (numdiv .eq. 1) return
          next235 = next235 + 2
          goto 100
          end
    """)
    c = fortc.generate_c(src)
    assert "fint FNAME(next235)(double *base)" in c
    assert "L100:" in c and "goto L100;" in c
    assert "(fint)((*base) / 2e0 + .9999e0)" in c


def test_complex_arithmetic_and_array(tmp_path):
    src = _write(tmp_path, "k.f", """
          subroutine kern(nd, charge, pot, rr)
          implicit none
          integer nd, ii
          real *8 rr, rtmp
          complex *16 charge(nd), pot(nd)
          rtmp = log(rr)/2
          do ii = 1, nd
             pot(ii) = pot(ii) + rtmp*charge(ii)
          enddo
          return
          end
    """)
    c = fortc.generate_c(src)
    assert "fcomplex *charge" in c and "fcomplex *pot" in c
    # left-associative add preserved (no x += a+b reassociation)
    assert "pot[ii - 1] = pot[ii - 1] + rtmp * charge[ii - 1];" in c


def test_integer_power_is_exact_not_pow(tmp_path):
    src = _write(tmp_path, "p.f", """
          subroutine sq(x, y)
          real *8 x, y
          y = x**2
          return
          end
    """)
    c = fortc.generate_c(src)
    assert "pow(" not in c
    assert "(*x) * (*x)" in c.replace("((*x)) * ((*x))", "(*x) * (*x)")


def test_only_subset_selection(tmp_path):
    src = _write(tmp_path, "two.f", """
          subroutine a(x)
          real *8 x
          x = 1
          return
          end
          subroutine b(x)
          real *8 x
          x = 2
          return
          end
    """)
    c = fortc.generate_c(src, only={"a"})
    assert "FNAME(a)" in c
    assert "FNAME(b)" not in c


def test_header_generation(tmp_path):
    src = _write(tmp_path, "h.f", """
          subroutine foo(x)
          real *8 x
          x = 1
          return
          end
    """)
    h = fortc.generate_h(src, runtime_header="rt.h", guard_prefix="MY_")
    assert "#ifndef MY_H_H" in h
    assert '#include "rt.h"' in h
    assert "void FNAME(foo)(double *x);" in h


def test_unsupported_is_loud(tmp_path):
    # a formatted WRITE is an executable statement fortc does not translate
    src = _write(tmp_path, "u.f", """
          subroutine u(x)
          real *8 x
          write(6,*) x
          return
          end
    """)
    try:
        fortc.generate_c(src)
    except fortc.Unsupported:
        return
    raise AssertionError("expected Unsupported on an untranslatable statement")
