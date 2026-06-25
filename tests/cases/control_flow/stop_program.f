c STOP: STOP statements (bare and with an integer code) must transpile and
c compile. Here the guards are never taken, so the program runs to completion
c and its stdout matches gfortran; the STOPs exercise the translation to exit().
      program main
      implicit none
      integer i, n
      n = 5
      do i = 1, n
        if (i .gt. n) stop
        print '(i0)', i
      enddo
      if (n .lt. 0) stop 2
      print '(a)', 'done'
      end
