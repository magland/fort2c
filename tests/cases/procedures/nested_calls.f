      program main
      implicit none
      double precision x, y
      x = 1.3d0
      call outer(x, y)
      print '(es24.16)', y
      end

      subroutine outer(x, y)
      implicit none
      double precision x, y, t
      call inner(x, t)
      call inner(t, y)
      return
      end

      subroutine inner(x, y)
      implicit none
      double precision x, y
      y = x * x + dsin(x)
      return
      end
