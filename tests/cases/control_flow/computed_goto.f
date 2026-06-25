c computed GOTO: goto (l1,l2,l3) i jumps to the i-th label, or falls through
c when i is out of range.
      program main
      implicit none
      integer i
      do i = 1, 4
         goto (10, 20, 30) i
         print '(i0)', 0
         goto 99
 10      print '(i0)', 100
         goto 99
 20      print '(i0)', 200
         goto 99
 30      print '(i0)', 300
 99      continue
      end do
      end
