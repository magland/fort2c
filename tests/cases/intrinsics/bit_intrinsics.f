c bit-manipulation intrinsics: iand/ior/ieor/not/ishft/ibset/ibclr/btest.
      program main
      implicit none
      integer a, b
      a = 60
      b = 13
      print '(i0)', iand(a, b)
      print '(i0)', ior(a, b)
      print '(i0)', ieor(a, b)
      print '(i0)', not(a)
      print '(i0)', ishft(a, 2)
      print '(i0)', ishft(a, -2)
      print '(i0)', ibset(0, 4)
      print '(i0)', ibclr(a, 2)
      if (btest(a, 2)) print '(i0)', 1
      if (.not. btest(a, 0)) print '(i0)', 2
      end
