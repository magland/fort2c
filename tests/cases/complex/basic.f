      program main
      implicit none
      complex*16 a, b, c
      a = (1.5d0, -2.0d0)
      b = (0.5d0, 3.0d0)
      c = a * b + a / b - a
      print '(es24.16,1x,es24.16)', dreal(c), dimag(c)
      c = a * a * a
      print '(es24.16,1x,es24.16)', dreal(c), dimag(c)
      call combine(a, b, c)
      print '(es24.16,1x,es24.16)', dreal(c), dimag(c)
      end

      subroutine combine(a, b, c)
      implicit none
      complex*16 a, b, c
      c = (a + b) / (a - b) + a * dconjg(b)
      return
      end
