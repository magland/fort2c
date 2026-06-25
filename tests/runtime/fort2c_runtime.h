/*
 * fort2c_runtime.h - minimal ABI support header for fort2c-generated C.
 *
 * Supplies exactly the helpers the emitted code references: the FNAME symbol
 * mangler, the fint / fcomplex types, and the column-major index macros.
 * Used by the differential test harness; see tests/test_differential.py.
 */
#ifndef FORT2C_RUNTIME_H
#define FORT2C_RUNTIME_H

#include <stdint.h>

#define FNAME(x) x##_              /* gfortran ABI: lowercase + trailing _ */

typedef int32_t         fint;      /* Fortran default integer            */
typedef int64_t         flong;     /* integer*8                          */
typedef double _Complex fcomplex;  /* complex*16 / double complex        */

/* column-major (Fortran) indexing for 1-based 2-/3-/4-D arrays */
#define FA2(i,j,ld1)              (((j)-1)*(ld1) + ((i)-1))
#define FA3(i,j,k,ld1,ld2)        ((((k)-1)*(ld2) + ((j)-1))*(ld1) + ((i)-1))
#define FA4(i,j,k,l,ld1,ld2,ld3)  (((((l)-1)*(ld3) + ((k)-1))*(ld2) + ((j)-1))*(ld1) + ((i)-1))

#endif /* FORT2C_RUNTIME_H */
