      program main
      implicit none
      double precision r
      call weighted(r)
      print '(es24.16)', r
      end

      subroutine weighted(r)
      implicit none
      integer i
      double precision w(4), x(4), r
      double precision half
      parameter (half = 0.5d0)
      data w / 1.0d0, 2.0d0, 3.0d0, 4.0d0 /
      data x / 10.0d0, 20.0d0, 30.0d0, 40.0d0 /
      r = 0.0d0
      do i = 1, 4
         r = r + w(i) * x(i) * half
      end do
      return
      end
