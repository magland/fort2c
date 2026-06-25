c array constructors: both the (/ ... /) and [ ... ] forms.
      program main
      implicit none
      double precision a(3), b(3)
      a = (/ 1.5d0, 2.5d0, 3.5d0 /)
      b = [ 10.0d0, 20.0d0, 30.0d0 ]
      print '(es24.16)', a(1) + a(2) + a(3)
      print '(es24.16)', b(1) + b(2) + b(3)
      end
