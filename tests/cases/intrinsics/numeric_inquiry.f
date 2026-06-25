c numeric inquiry: epsilon/huge/tiny (probed via log10 to avoid 3-digit
c exponents in the output) and the integer-valued bit_size/kind. Also the
c model-parameter intrinsics radix/digits/minexponent/maxexponent (-> FLT_RADIX,
c DBL_MANT_DIG, DBL_MIN_EXP, ... ; needed by LAPACK's DLAMCH); the result
c depends on the argument's kind, not its value.
      program main
      implicit none
      print '(es24.16)', epsilon(1.0d0)
      print '(es24.16)', log10(huge(1.0d0))
      print '(es24.16)', log10(tiny(1.0d0))
      print '(i0)', huge(1)
      print '(i0)', bit_size(1)
      print '(i0)', kind(1.0d0)
      print '(i0)', kind(1)
      print '(i0)', radix(1.0d0)
      print '(i0)', digits(1.0d0)
      print '(i0)', minexponent(1.0d0)
      print '(i0)', maxexponent(1.0d0)
      print '(i0)', radix(1.0)
      print '(i0)', digits(1.0)
      print '(i0)', minexponent(1.0)
      print '(i0)', maxexponent(1.0)
      end
