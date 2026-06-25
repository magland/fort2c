      program main
      implicit none
      integer m, n, i, j
      parameter (m = 3, n = 4)
      double precision a(m, n), t
      do j = 1, n
         do i = 1, m
            a(i, j) = dble(i) * 10.0d0 + dble(j)
         end do
      end do
      call trace_like(a, m, n, t)
      print '(es24.16)', t
      do j = 1, n
         do i = 1, m
            print '(i2,1x,i2,1x,es24.16)', i, j, a(i, j)
         end do
      end do
      end

      subroutine trace_like(a, m, n, t)
      implicit none
      integer m, n, i, j
      double precision a(m, n), t
      t = 0.0d0
      do j = 1, n
         do i = 1, m
            t = t + a(i, j) / dble(i + j)
         end do
      end do
      return
      end
