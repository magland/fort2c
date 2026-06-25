c hyperbolic functions and log10.
      program main
      implicit none
      double precision x
      x = 0.75d0
      print '(es24.16)', sinh(x)
      print '(es24.16)', cosh(x)
      print '(es24.16)', tanh(x)
      print '(es24.16)', dtanh(x)
      print '(es24.16)', log10(1000.0d0)
      print '(es24.16)', dlog10(2.0d0)
      end
