c DATA statement with an implied-do object list.
      program main
      implicit none
      integer i
      double precision a(5), s
      data (a(i), i = 1, 5) / 1.0d0, 2.0d0, 3.0d0, 4.0d0, 5.0d0 /
      s = 0.0d0
      do i = 1, 5
         s = s + a(i) * dble(i)
      end do
      print '(es24.16)', s
      end
