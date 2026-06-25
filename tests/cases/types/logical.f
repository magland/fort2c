c logical type: .true./.false. literals, .not., and a logical used in if.
      program main
      implicit none
      logical flag, other
      double precision y
      flag = .true.
      other = .not. flag
      if (flag .and. .not. other) then
         y = 1.0d0
      else
         y = 2.0d0
      end if
      print '(es24.16)', y
      end
