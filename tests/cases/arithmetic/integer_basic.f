      program main
      implicit none
      integer i, j, k
      i = 17
      j = 5
      k = i / j
      print '(i0)', k
      print '(i0)', mod(i, j)
      print '(i0)', i * j - i + j
      print '(i0)', (i + j) / (j - 2)
      end
