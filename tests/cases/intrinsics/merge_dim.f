c merge() (select by mask) and dim() (positive difference).
      program main
      implicit none
      double precision x, y
      x = 3.0d0
      y = 5.0d0
      print '(es24.16)', merge(x, y, x .gt. y)
      print '(es24.16)', merge(x, y, x .lt. y)
      print '(es24.16)', dim(x, y)
      print '(es24.16)', dim(y, x)
      print '(i0)', dim(7, 3)
      print '(i0)', merge(1, 0, x .lt. y)
      end
