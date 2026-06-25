      program main
      implicit none
      double precision s
      call shifted(s)
      print '(es24.16)', s
      end

      subroutine shifted(s)
      implicit none
      integer i
      double precision a(-3:3), s
      do i = -3, 3
         a(i) = dble(i) * dble(i) + 0.5d0
      end do
      s = 0.0d0
      do i = -3, 3
         s = s + a(i)
      end do
      return
      end
