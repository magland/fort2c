c an array section passed as an actual argument (sequence association: the
c callee receives the address of the section's first element).
      program main
      implicit none
      double precision a(6), s
      integer i
      do i = 1, 6
         a(i) = dble(i)
      end do
      call total(a(2:5), 4, s)
      print '(es24.16)', s
      call total(a(3:6), 4, s)
      print '(es24.16)', s
      end

      subroutine total(v, n, s)
      implicit none
      integer n, i
      double precision v(n), s
      s = 0.0d0
      do i = 1, n
         s = s + v(i)
      end do
      return
      end
