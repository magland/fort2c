c whole-array expressions: scalar broadcast, copy, and element-wise arithmetic.
      program main
      implicit none
      double precision a(4), b(4), c(4)
      integer i
      do i = 1, 4
         b(i) = dble(i)
      end do
      a = 0.0d0
      c = b * 2.0d0 - a
      a = c + b
      print '(es24.16)', a(1) + a(2) + a(3) + a(4)
      print '(es24.16)', c(1) + c(2) + c(3) + c(4)
      end
