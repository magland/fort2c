      program main
      implicit none
      integer i, n
      double precision x
      x = 1.5d0
      do n = 0, 6
         print '(i2,1x,es24.16)', n, x**n
      end do
      do i = 0, 5
         print '(i0)', 3**i
      end do
      print '(i0)', (-2)**5
      end
