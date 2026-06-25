      program main
      implicit none
      double precision x
      x = 2.6d0
      print '(i0)', int(x)
      print '(i0)', nint(x)
      print '(i0)', nint(-x)
      print '(i0)', idint(x + 1.7d0)
      print '(es24.16)', dble(7)
      print '(es24.16)', dfloat(5)
      end
