c integer abs()/iabs() return integers: abs(k)/2 is an integer divide (not a
c real one), and abs of an integer*8 stays 64-bit.
      program main
      implicit none
      integer k
      integer*8 m
      double precision x
      k = -7
      x = abs(k) / 2
      print '(es24.16)', x
      print '(i0)', iabs(k)
      m = 3000000
      m = -(m * m)
      print '(i0)', abs(m)
      end
