      program main
      implicit none
      integer n
      double precision s
      n = 10
      call build_and_sum(n, s)
      print '(es24.16)', s
      end

      subroutine build_and_sum(n, s)
      implicit none
      integer n, i
      double precision s
      double precision, allocatable :: a(:)
      allocate(a(n))
      do i = 1, n
         a(i) = dble(i) * dble(i) - 0.5d0
      end do
      s = 0.0d0
      do i = 1, n
         s = s + a(i)
      end do
      deallocate(a)
      return
      end
