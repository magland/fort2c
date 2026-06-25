c WHERE masked array assignment: single-line, and block form with ELSEWHERE.
      program main
      implicit none
      integer i
      double precision a(5), b(5)
      do i = 1, 5
         a(i) = dble(i) - 3.0d0
      end do
      b = 0.0d0
      where (a .gt. 0.0d0) b = a * 2.0d0
      print '(es24.16)', b(1) + b(2) + b(3) + b(4) + b(5)
      where (a .lt. 0.0d0)
         b = -a
      elsewhere
         b = 100.0d0
      end where
      print '(es24.16)', b(1) + b(2) + b(3) + b(4) + b(5)
      end
