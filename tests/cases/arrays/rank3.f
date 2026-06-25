      program main
      implicit none
      integer l, m, n, i, j, k
      parameter (l = 2, m = 3, n = 2)
      double precision a(l, m, n), s
      do k = 1, n
         do j = 1, m
            do i = 1, l
               a(i, j, k) = dble(i) + 2.0d0 * dble(j) + 4.0d0 * dble(k)
            end do
         end do
      end do
      call sum3(a, l, m, n, s)
      print '(es24.16)', s
      end

      subroutine sum3(a, l, m, n, s)
      implicit none
      integer l, m, n, i, j, k
      double precision a(l, m, n), s
      s = 0.0d0
      do k = 1, n
         do j = 1, m
            do i = 1, l
               s = s + a(i, j, k)
            end do
         end do
      end do
      return
      end
