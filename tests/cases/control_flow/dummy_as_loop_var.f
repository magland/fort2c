c A scalar dummy argument (passed by reference -> a pointer in C) used as the
c DO loop control variable. fort2c must emit `(*idx)++`, not the mis-parsed
c `*idx++`. Mirrors LAPACK's DTRTRI singularity check (DO INFO = 1, N).
      program main
      implicit none
      integer k
      call countsq(5, k)
      print '(I0)', k
      call countsq(1, k)
      print '(I0)', k
      end

      subroutine countsq(n, idx)
      implicit none
      integer n, idx
      integer s
      s = 0
      do idx = 1, n
         s = s + idx * idx
      end do
      idx = s
      end
