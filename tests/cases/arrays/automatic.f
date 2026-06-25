      program main
      implicit none
      double precision s
      call work(5, s)
      print '(es24.16)', s
      call work(9, s)
      print '(es24.16)', s
      end

      subroutine work(n, s)
      implicit none
      integer n, i
      double precision s
      double precision tmp(n)
      do i = 1, n
         tmp(i) = 1.0d0 / dble(i) + dble(i)
      end do
      s = 0.0d0
      do i = 1, n
         s = s + tmp(i) * tmp(i)
      end do
      return
      end
