c select case: single value, value list, range, and default.
      program main
      implicit none
      integer i
      double precision y
      do i = 0, 8
         select case (i)
         case (1)
            y = 10.0d0
         case (2, 3)
            y = 20.0d0
         case (4:6)
            y = 30.0d0
         case default
            y = 90.0d0
         end select
         print '(i2,1x,es24.16)', i, y
      end do
      end
