c reshape (column-major) and cshift (circular shift).
      program main
      implicit none
      integer i
      double precision a(6), b(2,3), v(5), w(5), s
      do i = 1, 6
         a(i) = dble(i)
      end do
      b = reshape(a, (/2, 3/))
      print '(es24.16)', b(1,1) + b(2,1) * 2.0d0 + b(1,2) * 3.0d0
      do i = 1, 5
         v(i) = dble(i)
      end do
      w = cshift(v, 2)
      s = 0.0d0
      do i = 1, 5
         s = s + w(i) * dble(i)
      end do
      print '(es24.16)', s
      w = cshift(v, -1)
      print '(es24.16)', w(1)
      end
