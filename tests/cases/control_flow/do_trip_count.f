c a DO loop's trip count is fixed when the loop is entered: modifying the bound
c (and the step) in the body must not change how many times it runs.
      program main
      implicit none
      integer i, n, cnt, istep
      n = 5
      cnt = 0
      do i = 1, n
         n = n + 1
         cnt = cnt + 1
         if (cnt .gt. 1000) exit
      end do
      print '(i0)', cnt
      cnt = 0
      istep = 2
      do i = 1, 10, istep
         istep = istep + 1
         cnt = cnt + 1
      end do
      print '(i0)', cnt
      end
