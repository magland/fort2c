      program main
      implicit none
      integer ifib
      double precision dpoly
      complex*16 crot
      complex*16 z
      print '(i0)', ifib(10)
      print '(es24.16)', dpoly(2.0d0)
      z = crot((1.0d0, 0.0d0))
      print '(es24.16,1x,es24.16)', dreal(z), dimag(z)
      end

      function ifib(n)
      implicit none
      integer ifib, n, i, a, b, t
      a = 0
      b = 1
      do i = 1, n
         t = a + b
         a = b
         b = t
      end do
      ifib = a
      return
      end

      function dpoly(x)
      implicit none
      double precision dpoly, x
      dpoly = 3.0d0 * x**3 - 2.0d0 * x**2 + x - 7.0d0
      return
      end

      function crot(z)
      implicit none
      complex*16 crot, z
      crot = z * dcmplx(0.0d0, 1.0d0)
      return
      end
