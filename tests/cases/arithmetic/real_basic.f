      program main
      implicit none
      double precision a, b, c, r
      a = 3.5d0
      b = 2.0d0
      c = 7.25d0
c     operator precedence and division must be preserved exactly
      r = a + b * c - a / b
      print '(es24.16)', r
      r = (a + b) * (c - a) / (b + 1.0d0)
      print '(es24.16)', r
      r = -a + b - c
      print '(es24.16)', r
      end
