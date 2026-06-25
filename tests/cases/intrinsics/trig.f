      program main
      implicit none
      double precision x, pi
      pi = 4.0d0 * datan(1.0d0)
      x = pi / 5.0d0
      print '(es24.16)', dsin(x)
      print '(es24.16)', dcos(x)
      print '(es24.16)', dtan(x)
      print '(es24.16)', datan2(2.0d0, 3.0d0)
      print '(es24.16)', asin(0.5d0)
      print '(es24.16)', acos(0.25d0)
      print '(es24.16)', pi
      end
