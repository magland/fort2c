c matmul: c = matmul(a, b) for 2-D matrices.
      program main
      implicit none
      integer i, j
      double precision a(2,3), b(3,2), c(2,2), s
      do j = 1, 3
         do i = 1, 2
            a(i,j) = dble(i) + dble(j)
         end do
      end do
      do j = 1, 2
         do i = 1, 3
            b(i,j) = dble(i) - dble(j)
         end do
      end do
      c = matmul(a, b)
      s = 0.0d0
      do j = 1, 2
         do i = 1, 2
            s = s + c(i,j) * dble(i * 10 + j)
         end do
      end do
      print '(es24.16)', s
      end
