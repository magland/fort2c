      program main
      implicit none
      double precision x
      x = 3.0d0
      print '(es24.16)', dexp(x)
      print '(es24.16)', dlog(x)
      print '(es24.16)', dsqrt(x)
      print '(es24.16)', dabs(-x)
      print '(es24.16)', dexp(dlog(x))
      print '(es24.16)', log(exp(x) + 1.0d0)
      end
