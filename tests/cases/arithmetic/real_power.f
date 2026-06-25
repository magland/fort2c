      program main
      implicit none
      double precision x, y
      x = 2.0d0
      y = 0.75d0
      print '(es24.16)', x**y
      print '(es24.16)', x**(-y)
      print '(es24.16)', (x + 1.0d0)**1.5d0
      end
