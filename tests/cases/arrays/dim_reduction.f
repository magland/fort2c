c sum/maxval/minval/product with a DIM argument (reduce a 2-D array to 1-D).
      program main
      implicit none
      integer i, j
      double precision a(2,3), r1(3), r2(2), p(3), mx(3)
      do j = 1, 3
         do i = 1, 2
            a(i,j) = dble(i) * 10.0d0 + dble(j)
         end do
      end do
      r1 = sum(a, 1)
      r2 = sum(a, 2)
      p = product(a, 1)
      mx = maxval(a, 1)
      print '(es24.16)', r1(1) + r1(2) + r1(3)
      print '(es24.16)', r2(1) * 2.0d0 + r2(2)
      print '(es24.16)', p(1) + p(2) + p(3)
      print '(es24.16)', mx(1) + mx(2) + mx(3)
      end
