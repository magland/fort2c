c integer*8 must use 64 bits: this product overflows 32-bit but not 64-bit.
      program main
      implicit none
      integer*8 m, p
      m = 2000000000
      m = m + m
      print '(i0)', m
      p = 3000000
      p = p * p
      print '(i0)', p
      end
