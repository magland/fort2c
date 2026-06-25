c modulo() takes the sign of the divisor (unlike mod / C's %), for both
c integer and real arguments.
      program main
      implicit none
      print '(i0)', modulo(-7, 3)
      print '(i0)', modulo(7, -3)
      print '(i0)', mod(-7, 3)
      print '(es24.16)', modulo(-7.5d0, 3.0d0)
      print '(es24.16)', modulo(7.5d0, -3.0d0)
      end
