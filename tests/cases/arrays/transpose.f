c the TRANSPOSE array intrinsic (b = transpose(a)).
      program main
      implicit none
      integer i, j
      double precision a(2,3), b(3,2), s
      do j = 1, 3
         do i = 1, 2
            a(i,j) = dble(i) * 10.0d0 + dble(j)
         end do
      end do
      b = transpose(a)
      s = 0.0d0
      do j = 1, 2
         do i = 1, 3
            s = s + b(i,j) * dble(i + j)
         end do
      end do
      print '(es24.16)', s
      end
