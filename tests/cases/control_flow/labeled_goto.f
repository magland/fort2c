      program main
      implicit none
      integer next235
      double precision base
      base = 100.0d0
      print '(i0)', next235(base)
      base = 2000.0d0
      print '(i0)', next235(base)
      end

      function next235(base)
      implicit none
      integer next235, numdiv
      double precision base
      next235 = 2 * int(base / 2d0 + .9999d0)
      if (next235 .le. 0) next235 = 2
 100  numdiv = next235
      do while (mod(numdiv, 2) .eq. 0)
         numdiv = numdiv / 2
      end do
      do while (mod(numdiv, 3) .eq. 0)
         numdiv = numdiv / 3
      end do
      do while (mod(numdiv, 5) .eq. 0)
         numdiv = numdiv / 5
      end do
      if (numdiv .eq. 1) return
      next235 = next235 + 2
      goto 100
      end
