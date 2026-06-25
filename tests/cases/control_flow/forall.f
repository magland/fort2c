c FORALL statement and block construct.
      program main
      implicit none
      integer i
      double precision a(5), b(5), s
      forall (i = 1:5) a(i) = dble(i) * dble(i)
      forall (i = 1:5)
         b(i) = a(i) + 1.0d0
      end forall
      s = 0.0d0
      do i = 1, 5
         s = s + a(i) + b(i)
      end do
      print '(es24.16)', s
      end
