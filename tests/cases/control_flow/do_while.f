      program main
      implicit none
      double precision x
      integer cnt
      call collatzish(27.0d0, x, cnt)
      print '(i0,1x,es24.16)', cnt, x
      end

      subroutine collatzish(x0, x, cnt)
      implicit none
      double precision x0, x
      integer cnt
      x = x0
      cnt = 0
      do while (x .gt. 1.0d0)
         x = x * 0.5d0 + 0.1d0
         cnt = cnt + 1
      end do
      return
      end
