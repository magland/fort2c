c an implied-do output list with a runtime bound becomes a runtime print loop.
      program main
      implicit none
      integer i, n
      double precision a(5)
      n = 5
      do i = 1, n
         a(i) = dble(i) * 0.5d0
      end do
      print '(es24.16)', (a(i), i = 1, n)
      print '(i2,1x,es24.16)', (i, a(i), i = 1, n)
      end
