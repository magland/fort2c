c ENTRY: a subroutine with an alternate entry point. `mm` forms a*b; the
c entry `mmt` forms a*b^T. Both share the dummy args and the loop locals,
c and each is emitted as its own C function. (Mirrors legeexps' matmul/matmua.)
      program main
      implicit none
      double precision a(2,2), b(2,2), c(2,2)
      a(1,1) = 1d0
      a(2,1) = 2d0
      a(1,2) = 3d0
      a(2,2) = 4d0
      b(1,1) = 5d0
      b(2,1) = 6d0
      b(1,2) = 7d0
      b(2,2) = 8d0
      call mm(a, b, c, 2)
      print '(4f9.2)', c(1,1), c(2,1), c(1,2), c(2,2)
      call mmt(a, b, c, 2)
      print '(4f9.2)', c(1,1), c(2,1), c(1,2), c(2,2)
      end

      subroutine mm(a, b, c, n)
      implicit double precision (a-h,o-z)
      dimension a(n,n), b(n,n), c(n,n)
      do i = 1, n
      do j = 1, n
      d = 0
      do k = 1, n
      d = d + a(i,k) * b(k,j)
      enddo
      c(i,j) = d
      enddo
      enddo
      return
      entry mmt(a, b, c, n)
      do i = 1, n
      do j = 1, n
      d = 0
      do k = 1, n
      d = d + a(i,k) * b(j,k)
      enddo
      c(i,j) = d
      enddo
      enddo
      return
      end
