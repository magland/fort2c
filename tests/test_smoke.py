"""Smoke tests: transpile small Fortran snippets and check the emitted C."""

import textwrap

import fort2c


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
    c = fort2c.generate_c(src)
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
    c = fort2c.generate_c(src)
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
    c = fort2c.generate_c(src)
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
    c = fort2c.generate_c(src, only={"a"})
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
    h = fort2c.generate_h(src, runtime_header="rt.h", guard_prefix="MY_")
    assert "#ifndef MY_H_H" in h
    assert '#include "rt.h"' in h
    assert "void FNAME(foo)(double *x);" in h


def test_entry_emits_separate_functions(tmp_path):
    # An ENTRY point becomes its own C function, sharing the parent routine's
    # declarations but with the entry's own name and argument list.
    src = _write(tmp_path, "mm.f", """
          subroutine mm(a, b, c, n)
          implicit double precision (a-h,o-z)
          dimension a(n,n), b(n,n), c(n,n)
          do i = 1, n
          do j = 1, n
          d = 0
          do k = 1, n
          d = d + a(i,k) * b(k,j)
          enddo
          c(i,j) = d
          enddo
          enddo
          return
          entry mmt(a, b, c, n)
          do i = 1, n
          do j = 1, n
          d = 0
          do k = 1, n
          d = d + a(i,k) * b(j,k)
          enddo
          c(i,j) = d
          enddo
          enddo
          return
          end
    """)
    c = fort2c.generate_c(src)
    assert "void FNAME(mm)(double *a, double *b, double *c, fint *n)" in c
    assert "void FNAME(mmt)(double *a, double *b, double *c, fint *n)" in c
    # the entry's body is the transpose form: b is indexed (j,k), not (k,j)
    assert "b[FA2(j, k, (*n))]" in c
    # the prototype appears in the header too
    h = fort2c.generate_h(src)
    assert "void FNAME(mmt)(double *a, double *b, double *c, fint *n);" in h


def test_stop_maps_to_exit(tmp_path):
    # STOP halts: a bare STOP -> exit(0); an integer code -> exit(code).
    src = _write(tmp_path, "s.f", """
          subroutine s(flag)
          integer flag
          if (flag .eq. 1) stop
          if (flag .eq. 2) stop 3
          return
          end
    """)
    c = fort2c.generate_c(src)
    assert "exit(0);" in c
    assert "exit(3);" in c
    assert "#include <stdlib.h>" in c


def test_format_statement_is_stripped(tmp_path):
    # A labeled FORMAT statement (used by a subroutine WRITE that is itself
    # stripped as a diagnostic) has no C form and must not raise Unsupported.
    src = _write(tmp_path, "w.f", """
          subroutine w(x)
          real *8 x
          write(6, 100) x
 100      format('x = ', f8.3)
          return
          end
    """)
    c = fort2c.generate_c(src)
    assert "void FNAME(w)(double *x)" in c
    # neither the FORMAT spec nor the stripped WRITE leaves any artifact
    assert "f8.3" not in c
    assert "x = " not in c


def test_intrinsic_result_passed_by_reference(tmp_path):
    # Passing a conversion intrinsic (int8 -> INTEGER*8) by reference: the
    # result is an rvalue, so it must be materialized into a typed temp and the
    # temp's address passed -- never &int8(...).
    src = _write(tmp_path, "r.f", """
          subroutine r(n, a, b, c)
          integer *8 n
          double precision a(*), b(*)
          integer *8 c(*)
          call dreorderf(int8(3), n, a, b, c)
          return
          end
    """)
    c = fort2c.generate_c(src)
    assert "flong f2c_arg1 = (flong)(3);" in c
    assert "dreorderf_(&f2c_arg1, " in c
    assert "&int8" not in c          # no address-of an rvalue call result


def test_unsupported_is_loud(tmp_path):
    # a READ is an executable statement fort2c does not translate
    src = _write(tmp_path, "u.f", """
          subroutine u(x)
          real *8 x
          read(5,*) x
          return
          end
    """)
    try:
        fort2c.generate_c(src)
    except fort2c.Unsupported:
        return
    raise AssertionError("expected Unsupported on an untranslatable statement")
