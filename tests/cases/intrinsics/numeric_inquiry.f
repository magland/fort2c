c numeric inquiry: epsilon/huge/tiny (probed via log10 to avoid 3-digit
c exponents in the output) and the integer-valued bit_size/kind.
      program main
      implicit none
      print '(es24.16)', epsilon(1.0d0)
      print '(es24.16)', log10(huge(1.0d0))
      print '(es24.16)', log10(tiny(1.0d0))
      print '(i0)', huge(1)
      print '(i0)', bit_size(1)
      print '(i0)', kind(1.0d0)
      print '(i0)', kind(1)
      end
