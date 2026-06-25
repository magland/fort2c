c COUNT/ALL/ANY reductions over an array mask, plus MAXLOC/MINLOC.
      program main
      implicit none
      integer i
      double precision a(5)
      do i = 1, 5
         a(i) = dble(i) - 3.0d0
      end do
      print '(i0)', count(a .gt. 0.0d0)
      if (any(a .gt. 0.0d0)) print '(i0)', 1
      if (all(a .lt. 10.0d0)) print '(i0)', 2
      if (.not. all(a .gt. 0.0d0)) print '(i0)', 3
      print '(i0)', maxloc(a, 1)
      print '(i0)', minloc(a, 1)
      end
