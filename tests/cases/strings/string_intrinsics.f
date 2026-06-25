c string intrinsics: trim, index, ichar/iachar, char/achar, adjustl/adjustr.
      program main
      implicit none
      character*8 a, s
      character*16 c
      a = 'hi'
      print '(a)', trim(a)
      print '(i0)', len(trim(a))
      c = trim(a) // 'X'
      print '(a)', c
      print '(i0)', ichar('A')
      print '(i0)', iachar('z')
      s = char(72) // char(105)
      print '(a)', s
      s = 'abcdefcd'
      print '(i0)', index(s, 'cd')
      print '(i0)', index(s, 'zz')
      s = '  hi'
      print '(a)', adjustl(s)
      print '(i0)', len_trim(adjustl(s))
      s = 'hi  '
      print '(a)', adjustr(s)
      end
