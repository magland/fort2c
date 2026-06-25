c PRODUCT and DOT_PRODUCT reductions (whole array and section).
      program main
      implicit none
      integer i
      double precision a(5), b(4), c(4)
      do i = 1, 5
         a(i) = dble(i)
      end do
      do i = 1, 4
         b(i) = dble(i)
         c(i) = dble(i) * 2.0d0
      end do
      print '(es24.16)', product(a)
      print '(es24.16)', product(a(2:4))
      print '(es24.16)', dot_product(b, c)
      end
