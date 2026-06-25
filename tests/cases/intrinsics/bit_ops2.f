c more bit intrinsics: ibits (extract), ishftc (circular shift), and the
c mvbits subroutine (copy a bit field), plus dprod.
      program main
      implicit none
      integer a, b
      real x, y
      a = 60
      print '(i0)', ibits(a, 2, 4)
      print '(i0)', ishftc(a, 4, 8)
      print '(i0)', ishftc(a, -2, 8)
      b = 0
      call mvbits(7, 0, 3, b, 4)
      print '(i0)', b
      x = 1.5e0
      y = 3.0e0
      print '(es24.16)', dprod(x, y)
      end
