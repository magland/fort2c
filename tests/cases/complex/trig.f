c     Generic trig/hyperbolic intrinsics on complex arguments must use
c     the complex C functions, not the real ones (which drop the
c     imaginary part).
      program main
      implicit none
      complex*16 z, r
      z = (0.3d0, 3.7d0)
      r = sin(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      r = cos(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      r = tan(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      r = sinh(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      r = cosh(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      r = tanh(z)
      print '(es24.16,1x,es24.16)', dreal(r), dimag(r)
      end
