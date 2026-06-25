c Character literals and a // concatenation passed as ACTUAL ARGUMENTS: to a
c LOGICAL function used inside an expression, and to an INTEGER function. This
c is the pervasive LAPACK/BLAS pattern (LSAME(UPLO,'U'), ILAENV(...,UPLO//DIAG,
c ...)). Earlier fort2c rejected a Char_Literal_Constant reaching expr(), and
c had no concatenation form in _char_operand.
      program main
      implicit none
      logical samech
      integer twocode
      if ( samech('N', 'N') ) print '(A)', 'eq'
      if ( .not. samech('A', 'B') ) print '(A)', 'ne'
      print '(I0)', twocode('U' // 'N')
      print '(I0)', twocode('LL')
      end

      logical function samech(a, b)
      implicit none
      character a, b
      samech = a .eq. b
      end

      integer function twocode(s)
      implicit none
      character*(*) s
      twocode = ichar(s(1:1)) * 1000 + ichar(s(2:2))
      end
