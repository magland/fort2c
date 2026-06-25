      program main
      implicit none
      integer classify
      print '(i0)', classify(-5)
      print '(i0)', classify(0)
      print '(i0)', classify(7)
      end

      function classify(n)
      implicit none
      integer classify, n
      if (n) 10, 20, 30
 10   classify = -1
      return
 20   classify = 0
      return
 30   classify = 1
      return
      end
