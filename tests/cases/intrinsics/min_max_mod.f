      program main
      implicit none
      double precision a, b, c
      a = 3.5d0
      b = -2.0d0
      c = 9.0d0
      print '(es24.16)', max(a, b, c)
      print '(es24.16)', min(a, b, c)
      print '(es24.16)', dmod(c, a)
      print '(es24.16)', sign(a, b)
      print '(i0)', max(3, 7, 1)
      print '(i0)', min(3, 7, 1)
      print '(i0)', mod(17, 5)
      print '(i0)', isign(4, -2)
      end
