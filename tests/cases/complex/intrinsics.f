      program main
      implicit none
      complex*16 z, w
      double precision x
      z = dcmplx(2.0d0, -1.0d0)
      w = dconjg(z)
      print '(es24.16,1x,es24.16)', dreal(w), dimag(w)
      x = cdabs(z)
      print '(es24.16)', x
      x = abs(z)
      print '(es24.16)', x
      w = cdsqrt(z)
      print '(es24.16,1x,es24.16)', dreal(w), dimag(w)
      w = cdexp(z)
      print '(es24.16,1x,es24.16)', dreal(w), dimag(w)
      end
