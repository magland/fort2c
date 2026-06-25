c output lists: a whole array, and an implied-do, are expanded to their
c elements (with format reversion when the format is shorter than the list).
      program main
      implicit none
      integer i
      double precision a(4)
      do i = 1, 4
         a(i) = dble(i) * 1.25d0
      end do
      print '(4es24.16)', a
      print '(es24.16)', (a(i), i = 1, 4)
      print '(i2,1x,es24.16)', (i, a(i), i = 1, 4)
      end
