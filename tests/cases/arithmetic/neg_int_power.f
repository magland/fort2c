c integer base ** (runtime) integer exponent, including negative exponents
c (result is 0 unless the base is +/-1).
      program main
      implicit none
      integer j, n
      n = -2
      j = 2 ** n
      print '(i0)', j
      n = -3
      j = (-1) ** n
      print '(i0)', j
      n = 5
      j = 2 ** n
      print '(i0)', j
      end
