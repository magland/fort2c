c sum/maxval/minval over a whole multi-dimensional array (flat reduction).
      program main
      implicit none
      integer i, j
      double precision a(3, 4)
      do j = 1, 4
         do i = 1, 3
            a(i, j) = dble(i) + dble(j) * 0.5d0
         end do
      end do
      print '(es24.16)', sum(a)
      print '(es24.16)', maxval(a)
      print '(es24.16)', minval(a)
      end
