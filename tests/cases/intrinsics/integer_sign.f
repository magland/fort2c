c sign()/isign() of integers stay integer: sign(7,-2)/2 is an integer divide.
      program main
      implicit none
      double precision x
      x = sign(7, -2) / 2
      print '(es24.16)', x
      print '(i0)', sign(5, 3)
      print '(i0)', isign(5, -3)
      end
