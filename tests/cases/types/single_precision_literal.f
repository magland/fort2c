c single-precision literal arithmetic: 1.0/3.0 is a REAL (single) divide that
c is then widened to double; the 'd' literals stay in double precision.
      program main
      implicit none
      double precision y, z
      y = 1.0/3.0
      z = 1.0d0/3.0d0
      print '(es24.16)', y
      print '(es24.16)', z
      end
