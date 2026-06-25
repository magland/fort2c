c statement functions  f(x) = expr  are inlined at each use site.
      program main
      implicit none
      double precision sq, x, area
      integer isq, i
      sq(x) = x * x
      isq(i) = i * i
      area = sq(3.0d0) + sq(2.5d0)
      print '(es24.16)', area
      print '(i0)', isq(7)
      end
