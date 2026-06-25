c ASSOCIATE: a variable/element alias, and a once-evaluated expression.
      program main
      implicit none
      double precision a(3), s, p, q
      integer i
      do i = 1, 3
         a(i) = dble(i)
      end do
      s = 0.0d0
      do i = 1, 3
         associate (x => a(i))
            s = s + x * x
         end associate
      end do
      print '(es24.16)', s
      p = 2.0d0
      q = 3.0d0
      associate (r => p * q + 1.0d0)
         print '(es24.16)', r * r
      end associate
c     alias allows assignment through the associate name
      associate (a1 => a(1))
         a1 = 42.0d0
      end associate
      print '(es24.16)', a(1)
      end
