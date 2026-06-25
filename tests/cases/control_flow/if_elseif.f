      program main
      implicit none
      integer i
      double precision grade
      do i = 1, 5
         call bucket(dble(i) * 20.0d0 - 5.0d0, grade)
         print '(i2,1x,es24.16)', i, grade
      end do
      end

      subroutine bucket(score, grade)
      implicit none
      double precision score, grade
      if (score .lt. 20.0d0) then
         grade = 0.0d0
      else if (score .lt. 40.0d0) then
         grade = 1.0d0
      else if (score .lt. 60.0d0) then
         grade = 2.0d0
      else if (score .lt. 80.0d0) then
         grade = 3.0d0
      else
         grade = 4.0d0
      end if
      return
      end
