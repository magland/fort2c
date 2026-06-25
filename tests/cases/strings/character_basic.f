c CHARACTER scalars: assignment (blank-padded), comparison, len/len_trim, and
c A / Aw output.
      program main
      implicit none
      character*8 a, b
      character*1 flag
      a = 'Hello'
      b = 'Hello'
      flag = 'N'
      if (a .eq. b) flag = 'Y'
      print '(a)', a
      print '(a10)', a
      print '(a3)', a
      print '(a)', flag
      print '(i0)', len(a)
      print '(i0)', len_trim(a)
      if (a .ne. 'World') print '(i0)', 1
      if (a .lt. 'World') print '(i0)', 2
      end
