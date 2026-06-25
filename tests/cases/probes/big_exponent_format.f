c PROBE: for a 3-digit decimal exponent (|exp| >= 100) Fortran's ES descriptor
c        drops the 'E' (e.g. 1.79..+308), but C's %E keeps it (1.79..E+308).
c        This cannot be reproduced with a plain printf conversion.
      program main
      implicit none
      print '(es24.16)', huge(1.0d0)
      print '(es24.16)', 1.0d-200
      end
