      program main
      implicit none
      integer n, i
      parameter (n = 8)
      double precision a(n), s
      do i = 1, n
         a(i) = dsqrt(dble(i)) * 1.25d0
      end do
      call accum(a, n, s)
      print '(es24.16)', s
      do i = 1, n
         print '(i2,1x,es24.16)', i, a(i)
      end do
      end

      subroutine accum(a, n, s)
      implicit none
      integer n, i
      double precision a(n), s
      s = 0.0d0
      do i = 1, n
         s = s + a(i) * a(i)
      end do
      return
      end
