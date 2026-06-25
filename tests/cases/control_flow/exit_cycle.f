      program main
      implicit none
      double precision s
      call partial(s)
      print '(es24.16)', s
      end

      subroutine partial(s)
      implicit none
      integer i
      double precision s
      s = 0.0d0
      do i = 1, 100
         if (mod(i, 7) .eq. 0) cycle
         if (i .gt. 40) exit
         s = s + 1.0d0 / dble(i)
      end do
      return
      end
