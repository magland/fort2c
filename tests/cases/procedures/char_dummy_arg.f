c character dummy arguments (assumed length *(*)) pass a hidden length; also
c a typed `integer function` result.
      program main
      implicit none
      character*8 a, b
      integer m, slen
      a = 'hello'
      b = 'world'
      call combine(a, b, m)
      print '(i0)', m
      print '(i0)', slen(a)
      end
      subroutine combine(x, y, n)
      implicit none
      character*(*) x, y
      integer n
      n = len_trim(x) + len_trim(y) + len(x)
      return
      end
      integer function slen(c)
      implicit none
      character*(*) c
      slen = len(c)
      return
      end
