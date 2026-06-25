c real*4 / default real are single precision: storing a double result through a
c single variable rounds it to a 24-bit mantissa, and sqrt is the single-
c precision sqrtf.
      program main
      implicit none
      real r
      double precision x
      x = 2.0d0
      r = x / 3.0d0
      print '(es24.16)', dble(r)
      r = sqrt(r)
      print '(es24.16)', dble(r)
      end
