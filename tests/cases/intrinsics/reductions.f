      program main
      implicit none
      integer n, i
      parameter (n = 6)
      double precision a(n)
      do i = 1, n
         a(i) = dcos(dble(i)) * 3.0d0
      end do
      print '(es24.16)', sum(a)
      print '(es24.16)', maxval(a)
      print '(es24.16)', minval(a)
      end
