c character concatenation (//) and substrings.
      program main
      implicit none
      character*16 c
      character*5 a
      character*3 b
      a = 'abcde'
      b = 'XYZ'
      c = a // b
      print '(a)', c
      print '(i0)', len_trim(c)
      c = a(1:3) // '-' // b(2:3)
      print '(a)', c
      print '(i0)', len_trim(c)
      end
