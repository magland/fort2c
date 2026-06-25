c An allocatable array's shape is fixed at ALLOCATE. Reassigning a variable
c used in its bounds afterwards must not change indexing: a(i,j) keeps the
c leading dimension captured at allocation (n=4 here), not the later n=999.
      program main
      implicit none
      integer i, j, n
      double precision, allocatable :: a(:,:)
      n = 4
      allocate(a(n,3))
      do j = 1, 3
        do i = 1, n
          a(i,j) = 10.0d0*j + i
        enddo
      enddo
      n = 999
      print '(f7.1)', a(1,1)
      print '(f7.1)', a(3,2)
      print '(f7.1)', a(4,3)
      deallocate(a)
      end
