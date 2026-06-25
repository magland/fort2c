c SAVE (and the implicit save of a DATA-initialized local): a counter persists
c across calls instead of resetting.
      program main
      implicit none
      integer i, accum
      accum = 0
      do i = 1, 4
         call bump(accum)
      end do
      print '(i0)', accum
      end
      subroutine bump(out)
      implicit none
      integer out, counter
      save counter
      data counter /0/
      counter = counter + 1
      out = counter
      return
      end
