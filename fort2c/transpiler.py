"""
fort2c.transpiler - a deterministic Fortran -> C transpiler.

Front end : fparser2 (faithful source AST; preserves operation order, so
            bit-for-bit -O0 agreement with gfortran falls out structurally).
Back end  : a C emitter using the gfortran ABI (lowercase symbol with a
            trailing underscore via the FNAME macro, all arguments by pointer,
            column-major array indexing, 1-based loop variables), stripping
            OpenMP / logging / timing and preserving gotos.

Originally written to reproduce a hand translation of the fmm2d library
(Laplace, Helmholtz, biharmonic, Stokes, modified-biharmonic) bit-for-bit at
-O0. Every construct it cannot handle raises `Unsupported` with the offending
node, so coverage gaps are loud, never silent.
"""

import copy
import os
import re
import sys
import threading

from fparser.two.parser import ParserFactory
from fparser.common.readfortran import FortranFileReader
from fparser.two.utils import walk
import fparser.two.Fortran2003 as f03


class Unsupported(Exception):
    pass


def cls(node):
    return type(node).__name__


# numeric promotion order: integer < real < complex, narrower kind first.
_RANK = {'fint': 0, 'flong': 1, 'float': 2, 'double': 3, 'fcomplex': 4}


def _promote(a, b):
    return a if _RANK.get(a, 3) >= _RANK.get(b, 3) else b


# Fortran identifiers that would collide with a C keyword or a libc/libm
# symbol the emitter uses. Such a Fortran variable is renamed `<name>_v`
# consistently across its declaration and every use.
C_RESERVED = {
    'auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do',
    'double', 'else', 'enum', 'extern', 'float', 'for', 'goto', 'if',
    'inline', 'int', 'long', 'register', 'restrict', 'return', 'short',
    'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union',
    'unsigned', 'void', 'volatile', 'while',
    'pow', 'exp', 'log', 'log10', 'sin', 'cos', 'tan', 'asin', 'acos',
    'atan', 'atan2', 'sqrt', 'fabs', 'abs', 'fmod', 'round', 'trunc',
    'floor', 'ceil', 'copysign', 'sinh', 'cosh', 'tanh', 'cabs', 'cpow',
    'clog', 'cexp', 'csqrt', 'conj', 'creal', 'cimag', 'malloc', 'free',
    'exit', 'I',
}


def cname(name):
    # Fortran is case-insensitive (and gfortran lowercases every symbol), so
    # canonicalize identifiers to lower case before emitting them.
    n = name.lower()
    return n + '_v' if n in C_RESERVED else n


# --------------------------------------------------------------------------
# Symbol table
# --------------------------------------------------------------------------

class Sym:
    def __init__(self, name):
        self.name = name
        self.ctype = None          # 'fint' | 'double' | 'fcomplex'
        self.is_dummy = False
        self.intent = None         # 'in' | 'out' | 'inout' | None
        self.is_param = False
        self.is_alloc = False
        self.is_save = False       # SAVE attribute (persist across calls)
        self.char_len = None       # CHARACTER length (chars), if ctype 'char'
        self.dims = None           # list of (lo_expr, hi_expr) or None
        self.dim_c = None          # for an ALLOCATE'd array: list of
                                   # (lo_cstr, extent_cstr) captured at the
                                   # allocation, so a later reassignment of a
                                   # bound variable cannot change indexing
        self.value = None          # fparser expr (scalar parameter / data init)
        self.data_values = None    # list of value nodes (whole-array DATA init)
        self.data_map = None       # {c_offset: value node} (subscripted DATA)

    @property
    def is_array(self):
        return self.dims is not None or self.is_alloc


def _kind_number(kind_node):
    """The integer kind from a Kind_Selector ('*8', '(kind=4)', ...), or None."""
    if kind_node is None:
        return None
    for lit in walk(kind_node, f03.Int_Literal_Constant):
        try:
            return int(str(lit.children[0]))
        except (ValueError, IndexError):
            pass
    return None


def ctype_of_spec(name, kind_node=None):
    """Map a Fortran type (name + optional kind) to a C type.
    COMPLEX* -> fcomplex; INTEGER/LOGICAL -> fint (or flong at kind 8);
    DOUBLE PRECISION -> double; REAL -> float, but real*8 / real(kind=8) ->
    double (the precision fmm2d actually uses)."""
    n = name.upper()
    k = _kind_number(kind_node)
    if 'CHARACTER' in n:
        return 'char'
    if 'COMPLEX' in n:
        return 'fcomplex'
    if 'INTEGER' in n or 'LOGICAL' in n:
        return 'flong' if k == 8 else 'fint'
    if 'DOUBLE' in n:                 # DOUBLE PRECISION
        return 'double'
    return 'double' if k == 8 else 'float'   # REAL: *8 -> double, else single


def ctype_of_name(name):
    """Name-only mapping (no kind selector), used for implicit typing."""
    return ctype_of_spec(name, None)


class Scope:
    """One Fortran procedure."""

    def __init__(self, sub, file_procs):
        self.file_procs = file_procs     # set of procedure names in this file
        self.syms = {}
        self.args = []
        self.is_main = isinstance(sub, f03.Main_Program)
        self.is_function = isinstance(sub, f03.Function_Subprogram)
        self.result_name = None
        self.result_ctype = None
        self.implicit_none = False
        self.implicit_map = self._default_implicit()
        self.all_saved = False           # a bare `SAVE` saves every local

        stmt = sub.children[0]
        # Function_Stmt(prefix, Name, args, suffix) / Subroutine_Stmt(...)
        self.name = str(walk(stmt, f03.Name)[0]).lower()
        arglist = None
        for c in stmt.children:
            if cls(c) in ('Dummy_Arg_List',):
                arglist = c
        if arglist is not None:
            self.args = [str(n).lower() for n in walk(arglist, f03.Name)]
        elif self.is_function is False and not self.is_main:
            # subroutine with single dummy arg appears directly
            for c in stmt.children:
                if cls(c) == 'Name' and str(c).lower() != self.name:
                    self.args.append(str(c).lower())

        spec = None
        for c in sub.children:
            if cls(c) == 'Specification_Part':
                spec = c
        self.exec_part = None
        for c in sub.children:
            if cls(c) == 'Execution_Part':
                self.exec_part = c

        if spec is not None:
            self._parse_implicit(spec)
            self._parse_spec(spec)

        # dummy args: ensure typed (implicit if needed) and flagged
        for nm in self.args:
            s = self.sym(nm)
            s.is_dummy = True
            if s.ctype is None:
                s.ctype = self.implicit_ctype(nm)

        # a typed function statement (`integer function f(...)`) sets the result
        # type, unless an explicit declaration in the body already did
        if self.is_function:
            rsym = self.sym(self.name)
            if rsym.ctype is None:
                pt = self._function_prefix_ctype(stmt)
                if pt is not None:
                    rsym.ctype = pt

        # discover implicitly-typed local scalars referenced in the body
        self._discover_locals()

        # any symbol still untyped (e.g. a data/parameter var with no explicit
        # type declaration) gets its implicit type
        for s in self.syms.values():
            if s.ctype is None:
                s.ctype = self.implicit_ctype(s.name)

        if self.is_function:
            self.result_name = self.name
            rsym = self.sym(self.name)
            if rsym.ctype is None:
                rsym.ctype = self.implicit_ctype(self.name)
            self.result_ctype = rsym.ctype

        # character dummies pass a hidden length argument (gfortran ABI); an
        # assumed-length `*(*)` dummy takes its length from that argument.
        self.char_dummies = []
        for nm in self.args:
            s = self.get(nm)
            if s and s.ctype == 'char' and not s.is_array:
                self.char_dummies.append(nm)
                if s.char_len == '*':
                    s.char_len = f'{cname(nm)}_len'

        # statement functions:  f(x) = expr  (fparser reports these as leading
        # assignments to a scalar "element"); record name -> (params, body)
        self.stmt_funcs = {}
        self._find_stmt_funcs()

        # ENTRY points: the body emitted for this scope runs from exec_start to
        # exec_stop (a slice of exec_part.children). With no ENTRYs that is the
        # whole body; _find_entries narrows the main body to end at the first
        # ENTRY and records each entry's own (start, stop) slice + arg list.
        self.exec_start = 0
        self.exec_stop = None
        self.entries = self._find_entries()

    def _find_entries(self):
        """Discover ENTRY statements. Each ENTRY is translated to a separate C
        function sharing this routine's declarations but with its own name and
        argument list (see entry_scopes). Returns a list of
        {name, args, start, stop}; also sets self.exec_stop so the main routine
        stops at the first ENTRY.

        Only the standard "ENTRY after a RETURN" idiom is supported: each entry
        point (and the main routine) must terminate before the next ENTRY, so
        the segments don't fall through into one another. Anything else raises
        Unsupported rather than miscompile."""
        if self.exec_part is None:
            return []
        children = self.exec_part.children
        idxs = [i for i, st in enumerate(children) if cls(st) == 'Entry_Stmt']
        if not idxs:
            return []
        for i in idxs:
            if i == 0 or cls(children[i - 1]) not in ('Return_Stmt', 'Stop_Stmt'):
                raise Unsupported(
                    'ENTRY not preceded by RETURN/STOP (fall-through entries)')
        self.exec_stop = idxs[0]
        out = []
        for j, i in enumerate(idxs):
            names = [str(n).lower() for n in walk(children[i], f03.Name)]
            ename, eargs = names[0], names[1:]
            for nm in eargs:                 # type entry args (implicit if needed)
                s = self.sym(nm)
                if s.ctype is None:
                    s.ctype = self.implicit_ctype(nm)
            stop = idxs[j + 1] if j + 1 < len(idxs) else None
            out.append({'name': ename, 'args': eargs, 'start': i + 1,
                        'stop': stop})
        return out

    def _find_stmt_funcs(self):
        if self.exec_part is None:
            return
        for st in self.exec_part.children:
            if cls(st) != 'Assignment_Stmt':
                break          # statement functions are contiguous at the top
            lhs = st.children[0]
            if cls(lhs) != 'Part_Ref':
                break
            base = str(lhs.children[0])
            s = self.get(base)
            if s is None or s.is_array or s.is_alloc:
                break
            subs = list(lhs.children[1].children)
            if not subs or not all(cls(x) == 'Name' for x in subs):
                break
            self.stmt_funcs[base.lower()] = (
                [str(x).lower() for x in subs], st.children[2])

    def sym(self, name):
        name = name.lower()
        return self.syms.setdefault(name, Sym(name))

    def get(self, name):
        return self.syms.get(name.lower())

    # -- implicit typing ----------------------------------------------------

    def _default_implicit(self):
        # Fortran default: I-N integer, all other letters real (-> double here)
        m = {chr(c): 'double' for c in range(ord('a'), ord('z') + 1)}
        for c in range(ord('i'), ord('n') + 1):
            m[chr(c)] = 'fint'
        return m

    def _parse_implicit(self, spec):
        for st in walk(spec, f03.Implicit_Stmt):
            if str(st).strip().upper() == 'IMPLICIT NONE':
                self.implicit_none = True
        for ispec in walk(spec, f03.Implicit_Spec):
            tspec = ispec.children[0]
            ctype = ctype_of_spec(str(tspec.children[0]), tspec.children[1])
            for ls in walk(ispec, f03.Letter_Spec):
                lo = str(ls.children[0]).lower()
                hi = str(ls.children[1]).lower() if ls.children[1] else lo
                for c in range(ord(lo), ord(hi) + 1):
                    self.implicit_map[chr(c)] = ctype

    def implicit_ctype(self, name):
        return self.implicit_map.get(name[0].lower(), 'double')

    def _discover_locals(self):
        if self.exec_part is None:
            return
        part_ref_bases = {str(pr.children[0]).lower()
                          for pr in walk(self.exec_part, f03.Part_Ref)}
        call_names = {str(cs.children[0]).lower()
                      for cs in walk(self.exec_part, f03.Call_Stmt)}
        for nm_node in walk(self.exec_part, f03.Name):
            nm = str(nm_node).lower()
            if (nm in self.syms or nm in part_ref_bases
                    or nm in call_names or nm in self.file_procs):
                continue
            if self.implicit_none:
                # every variable must be declared; an unknown bare name is an
                # unrecognized intrinsic/function, not a variable -> skip
                continue
            s = self.sym(nm)
            s.ctype = self.implicit_ctype(nm)

    # -- specification part -------------------------------------------------

    def _parse_spec(self, spec):
        for tdecl in walk(spec, f03.Type_Declaration_Stmt):
            self._parse_type_decl(tdecl)
        for pstmt in walk(spec, f03.Parameter_Stmt):
            for ncd in walk(pstmt, f03.Named_Constant_Def):
                nm = str(ncd.children[0])
                s = self.sym(nm)
                s.is_param = True
                s.value = ncd.children[1]
        # bare `dimension a(..), b(..)` BEFORE data, so subscripted DATA on
        # those arrays can resolve column-major offsets
        for dim in walk(spec, f03.Dimension_Stmt):
            for nm_node, shape in dim.children[0]:
                self.sym(str(nm_node)).dims = self._array_spec_dims(shape)
        for sstmt in walk(spec, f03.Save_Stmt):
            ents = walk(sstmt, f03.Saved_Entity)
            names = [str(walk(e, f03.Name)[0]) for e in ents] if ents \
                else [str(n) for n in walk(sstmt.children[1], f03.Name)] \
                if sstmt.children[1] is not None else []
            if not names:
                self.all_saved = True        # bare SAVE: save every local
            for nm in names:
                self.sym(nm).is_save = True
        for dstmt in walk(spec, f03.Data_Stmt):
            self._parse_data(dstmt)

    def _parse_type_decl(self, tdecl):
        type_spec = tdecl.children[0]
        ctype = self._ctype_of(type_spec)
        char_len = self._char_len(type_spec) if ctype == 'char' else None
        attr_list = tdecl.children[1]
        intent = None
        is_param = False
        is_alloc = False
        is_save = False
        common_dims = None
        if attr_list is not None:
            for a in attr_list.children:
                k = cls(a)
                if k == 'Attr_Spec':
                    s = str(a).upper()
                    if s == 'PARAMETER':
                        is_param = True
                    elif s == 'ALLOCATABLE':
                        is_alloc = True
                    elif s == 'SAVE':
                        is_save = True
                elif k == 'Intent_Attr_Spec':
                    intent = str(walk(a, f03.Intent_Spec)[0]).lower()
                elif k == 'Dimension_Attr_Spec':
                    common_dims = self._array_spec_dims(a.children[1])
        for ent in walk(tdecl, f03.Entity_Decl):
            nm = str(ent.children[0])
            s = self.sym(nm)
            s.ctype = ctype
            if ctype == 'char':
                s.char_len = char_len
            s.intent = intent
            s.is_param = s.is_param or is_param
            s.is_alloc = s.is_alloc or is_alloc
            s.is_save = s.is_save or is_save
            ashape = ent.children[1]
            if ashape is not None:
                s.dims = self._array_spec_dims(ashape)
            elif common_dims is not None:
                s.dims = common_dims
            init = ent.children[3]
            if init is not None:
                # Initialization('=', expr)
                s.value = init.children[1]

    def _ctype_of(self, type_spec):
        # Intrinsic_Type_Spec(name, kind_selector)
        return ctype_of_spec(str(type_spec.children[0]), type_spec.children[1])

    def _function_prefix_ctype(self, stmt):
        # `integer function f(...)` etc.: the type spec in the Function_Stmt
        # prefix. char-valued functions are not supported (-> None).
        pre = stmt.children[0]
        if pre is None:
            return None
        for ts in walk(pre, f03.Intrinsic_Type_Spec):
            ct = ctype_of_spec(str(ts.children[0]), ts.children[1])
            return None if ct == 'char' else ct
        return None

    def _char_len(self, type_spec):
        # CHARACTER length from the Length_Selector (`*8` or `(len=8)`); the
        # default with no selector is 1; assumed length `*(*)` returns '*'
        # (resolved to the hidden length argument for a dummy).
        sel = type_spec.children[1]
        if sel is None:
            return 1
        lits = walk(sel, f03.Int_Literal_Constant)
        return int(str(lits[0].children[0])) if lits else '*'

    def _array_spec_dims(self, ashape):
        # assumed-size `a(..., lo:*)`: any preceding explicit dims, then a
        # final dim with a known lower bound and an unbounded (*) upper.
        asz = walk(ashape, f03.Assumed_Size_Spec)
        if asz:
            spec = asz[0]
            dims = []
            pre = spec.children[0]
            if pre is not None:
                for es in walk(pre, f03.Explicit_Shape_Spec):
                    dims.append(tuple(es.children))
            dims.append((spec.children[1], None))   # (lower, *)
            return dims
        dims = []
        for spec in walk(ashape, f03.Explicit_Shape_Spec):
            lo, hi = spec.children
            dims.append((lo, hi))
        # assumed-shape `:` (allocatable) -> unknown bounds, treat 1-based
        for spec in walk(ashape, f03.Assumed_Shape_Spec):
            dims.append((None, None))
        if not dims:
            # Deferred / a(:) sometimes parsed differently
            return [(None, None)]
        return dims

    def _parse_data(self, dstmt):
        for ds in walk(dstmt, f03.Data_Stmt_Set):
            objnode = ds.children[0]
            objs = (list(objnode.children)
                    if cls(objnode).endswith('_List') else [objnode])
            vals = self._data_values(ds.children[1])
            vi = 0
            for obj in objs:
                if vi >= len(vals):
                    break
                if cls(obj) == 'Data_Implied_Do':
                    vi = self._data_implied_do(obj, vals, vi)
                elif cls(obj) == 'Part_Ref':
                    # data a(i1), a(i2), .../v1, v2, .../  (subscripted elements)
                    s = self.sym(str(obj.children[0]))
                    if s.data_map is None:
                        s.data_map = {}
                    subs = list(obj.children[1].children)
                    s.data_map[self._data_offset(s, subs)] = vals[vi]
                    vi += 1
                else:                                   # bare Name
                    s = self.sym(str(obj))
                    if s.dims is not None:              # whole-array init
                        sz = self._array_size(s)
                        s.data_values = vals[vi:vi + sz]
                        vi += sz
                    else:                               # scalar
                        s.value = vals[vi]
                        vi += 1

    def _data_implied_do(self, node, vals, vi):
        # DATA (a(i), b(i)..., i = lo, hi[, step]) / values /
        objs = node.children[0]
        objs = (list(objs.children) if cls(objs).endswith('_List') else [objs])
        var = str(node.children[1]).lower()
        lo = const_eval_int(node.children[2], self)
        hi = const_eval_int(node.children[3], self)
        step = const_eval_int(node.children[4], self) if node.children[4] else 1
        for iv in range(lo, hi + (1 if step > 0 else -1), step):
            for obj in objs:                 # each is array(subscripts using var)
                if vi >= len(vals):
                    return vi
                s = self.sym(str(obj.children[0]))
                if s.data_map is None:
                    s.data_map = {}
                subs = list(obj.children[1].children)
                s.data_map[self._data_offset(s, subs, {var: iv})] = vals[vi]
                vi += 1
        return vi

    def _data_offset(self, s, subs, binds=None):
        # column-major C offset from constant subscripts
        off, stride = 0, 1
        for k, sub in enumerate(subs):
            lo = s.dims[k][0]
            loval = const_eval_int(lo, self) if lo is not None else 1
            off += (const_eval_int(sub, self, binds) - loval) * stride
            if k + 1 < len(subs):
                hi = const_eval_int(s.dims[k][1], self)
                stride *= (hi - loval + 1)
        return off

    def _array_size(self, s):
        sz = 1
        for lo, hi in s.dims:
            h = const_eval_int(hi, self)
            l = const_eval_int(lo, self) if lo is not None else 1
            sz *= (h - l + 1)
        return sz

    def _data_values(self, vlist_node):
        items = (list(vlist_node.children)
                 if cls(vlist_node).endswith('_List') else [vlist_node])
        out = []
        for it in items:
            if cls(it) == 'Data_Stmt_Value':
                rep, val = it.children
                out.extend([val] * (int(str(rep)) if rep is not None else 1))
            else:
                out.append(it)
        return out


# --------------------------------------------------------------------------
# C emitter
# --------------------------------------------------------------------------

REL_OPS = {
    '.EQ.': '==', '.NE.': '!=', '.LT.': '<', '.LE.': '<=',
    '.GT.': '>', '.GE.': '>=', '==': '==', '/=': '!=',
    '<': '<', '<=': '<=', '>': '>', '>=': '>=',
}
LOG_OPS = {'.AND.': '&&', '.OR.': '||', '.EQV.': '==', '.NEQV.': '!='}

# intrinsics whose result is double regardless of (real/complex) argument
INTRIN_DOUBLE_RESULT = {'DBLE', 'DREAL', 'DIMAG', 'CDABS', 'DABS', 'DSQRT',
                        'DLOG', 'DEXP', 'DATAN', 'DATAN2', 'DSIN', 'DCOS',
                        'DTAN', 'DASIN', 'DACOS', 'DSIGN', 'DINT', 'DNINT',
                        'DFLOAT', 'DMOD', 'DSINH', 'DCOSH', 'DTANH', 'DLOG10',
                        'ALOG10', 'DDIM', 'DPROD'}
# intrinsics whose result is single (default real)
INTRIN_SINGLE_RESULT = {'REAL', 'FLOAT', 'AIMAG', 'IMAG'}
# intrinsics whose result is integer
INTRIN_INT_RESULT = {'INT', 'IDINT', 'NINT', 'IDNINT', 'IABS', 'ISIGN',
                     'FLOOR', 'CEILING', 'BTEST', 'IDIM', 'COUNT', 'ALL',
                     'ANY', 'BIT_SIZE', 'KIND', 'MAXLOC', 'MINLOC', 'LEN',
                     'LEN_TRIM', 'INDEX', 'ICHAR', 'IACHAR'}
# generic real intrinsics whose result kind follows the argument's kind
INTRIN_FOLLOW_ARG = {'ABS', 'SQRT', 'LOG', 'EXP', 'ATAN', 'ATAN2', 'SIN',
                     'COS', 'TAN', 'ASIN', 'ACOS', 'SINH', 'COSH', 'TANH',
                     'LOG10', 'SIGN', 'AINT', 'ANINT', 'MOD', 'MODULO', 'DIM',
                     'MAX', 'MIN', 'IAND', 'IOR', 'IEOR', 'NOT', 'ISHFT',
                     'IBSET', 'IBCLR', 'IBITS', 'ISHFTC', 'SUM', 'PRODUCT',
                     'MAXVAL', 'MINVAL', 'EPSILON', 'TINY', 'HUGE'}
INTRIN_COMPLEX_RESULT = {'DCMPLX', 'CMPLX', 'DCONJG', 'CONJG', 'CDLOG',
                         'CDEXP', 'CDSQRT'}


# --------------------------------------------------------------------------
# Format (edit-descriptor) translation for PRINT / WRITE
#
# A deliberately small subset, chosen so the printf output matches gfortran's
# byte-for-byte: ES <-> %E, F <-> %f, I <-> %d (all verified identical),
# nX -> spaces, '...' literals, and / -> newline. Descriptors whose Fortran
# formatting differs from C (plain E/D/G normalization, EN, list-directed *)
# raise Unsupported so the gap is loud, never a silent wrong number.
# --------------------------------------------------------------------------

def _unquote_fortran(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        q = s[0]
        return s[1:-1].replace(q + q, q)
    return s


def _c_escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')


def _split_top_commas(fmt):
    parts, depth, cur = [], 0, ''
    for ch in fmt:
        if ch == '(':
            depth += 1; cur += ch
        elif ch == ')':
            depth -= 1; cur += ch
        elif ch == ',' and depth == 0:
            parts.append(cur); cur = ''
        else:
            cur += ch
    parts.append(cur)
    return parts


def _width_prec(s):
    s = s.strip()
    if s == '':
        return 0, None
    if '.' in s:
        a, b = s.split('.', 1)
        return (int(a) if a else 0), (int(b) if b else 0)
    return int(s), None


def parse_format(fmt):
    """Parse a Fortran format spec (without surrounding parens) into atoms:
    ('lit', text) | ('nl',) | ('data', c_conversion, kind)  with kind in
    {'i','f','a'}. A leading integer is a repeat count (n descriptors), except
    on X where it is the number of blanks and on / where it is the count."""
    atoms = []
    for tok in _split_top_commas(fmt):
        t = tok.strip()
        if t == '':
            continue
        m = re.match(r'(\d*)(.*)$', t, re.S)
        cnt = int(m.group(1)) if m.group(1) else 1
        rest = m.group(2).strip()
        if rest == '':
            continue
        c0 = rest[0]
        if c0 in "'\"":
            atoms.append(('lit', _unquote_fortran(rest)))
        elif c0 == '(':
            inner = rest[1:-1] if rest.endswith(')') else rest[1:]
            sub = parse_format(inner)
            for _ in range(cnt):
                atoms.extend(sub)
        elif c0 == '/':
            for _ in range(cnt):
                atoms.append(('nl',))
        elif c0 in 'xX':
            atoms.append(('lit', ' ' * cnt))
        elif c0 in 'iI':
            w, p = _width_prec(rest[1:])
            ws = '' if w == 0 else str(w)
            ps = '' if p is None else '.' + str(p)
            for _ in range(cnt):
                atoms.append(('data', '%' + ws + ps + 'd', 'i'))
        elif rest[:2].lower() == 'es':
            w, p = _width_prec(rest[2:])
            cf = '%' + ('' if w == 0 else str(w)) + '.' + str(p or 0) + 'E'
            for _ in range(cnt):
                atoms.append(('data', cf, 'f'))
        elif c0 in 'fF':
            w, p = _width_prec(rest[1:])
            cf = '%' + ('' if w == 0 else str(w)) + '.' + str(p or 0) + 'f'
            for _ in range(cnt):
                atoms.append(('data', cf, 'f'))
        elif c0 in 'aA':
            w = rest[1:].strip()
            aw = int(w) if w.isdigit() else None     # A or Aw field width
            for _ in range(cnt):
                atoms.append(('data', aw, 'a'))
        else:
            raise Unsupported(f'format edit descriptor {rest!r}')
    return atoms


class Emitter:
    def __init__(self, scope):
        self.s = scope
        self.tmp_n = 0
        self.pre = []          # statements to emit before current statement
        self.xcalls = {}       # cross-file callee name -> C return type
        self._sf_subst = {}    # statement-function param -> actual C expr
        self._assoc = {}       # ASSOCIATE name -> C expr
        self._assoc_ctype = {}  # ASSOCIATE name -> C type

    # -- expressions --------------------------------------------------------

    def expr(self, node):
        k = cls(node)
        if k == 'Name':
            return self.name_value(str(node))
        if k in ('Int_Literal_Constant', 'Signed_Int_Literal_Constant'):
            return str(node.children[0]) if k == 'Int_Literal_Constant' \
                else self._num_lit(node)
        if k in ('Real_Literal_Constant', 'Signed_Real_Literal_Constant'):
            txt = self._num_lit(node)
            return txt + 'f' if self._real_prec(node) == 'float' else txt
        if k == 'Logical_Literal_Constant':
            return '1' if str(node.children[0]).upper() == '.TRUE.' else '0'
        if k == 'Complex_Literal_Constant':
            re = self.expr(node.children[0])
            im = self.expr(node.children[1])
            return f'({re} + ({im}) * I)'
        if k == 'And_Operand' and isinstance(node.children[0], str):
            # unary .NOT.
            return f'!({self.expr(node.children[1])})'
        if k == 'Parenthesis':
            return '(' + self.expr(node.children[1]) + ')'
        if k == 'Part_Ref':
            return self.part_ref(node)
        if k in ('Intrinsic_Function_Reference', 'Function_Reference'):
            return self.func_ref(node)
        if k == 'Structure_Constructor':
            # dcmplx(a, b) / cmplx(a, b) misparsed as a derived-type ctor
            return self.struct_ctor(node)
        if k == 'Level_2_Unary_Expr':
            op, operand = node.children
            return f'{op}{self.expr(operand)}'
        # binary operator nodes: items = (lhs, op, rhs)
        items = node.children
        if len(items) == 3 and isinstance(items[1], str):
            return self.binop(items[0], items[1], items[2])
        raise Unsupported(f'expr node {k}: {node}')

    def binop(self, lhs, op, rhs):
        if op == '**':
            return self.power(lhs, rhs)
        if op in REL_OPS and (self.expr_ctype(lhs) == 'char'
                              or self.expr_ctype(rhs) == 'char'):
            pa, la = self._char_operand(lhs)
            pb, lb = self._char_operand(rhs)
            return f'(fmm_strcmp({pa}, {la}, {pb}, {lb}) {REL_OPS[op]} 0)'
        l = self.expr(lhs)
        r = self.expr(rhs)
        if op in ('+', '-', '*', '/'):
            return f'{l} {op} {r}'
        if op in REL_OPS:
            return f'{l} {REL_OPS[op]} {r}'
        if op in LOG_OPS:
            return f'{l} {LOG_OPS[op]} {r}'
        raise Unsupported(f'operator {op!r}')

    def power(self, base, exp):
        bt = self.expr_ctype(base)
        et = self.expr_ctype(exp)
        if self._is_neg_one(base) and et == 'fint':
            return f'(({self.expr(exp)}) % 2 == 0 ? 1 : -1)'
        n = self._int_lit_value(exp)
        if n is not None and n >= 0:
            # gfortran evaluates x**n (integer literal n) by exponentiation-by-
            # squaring, NOT pow(); reproduce that exact multiplication tree
            # so the last bit matches.
            return self._pow_by_squaring(base, n, bt)
        be = self.expr(base)
        ee = self.expr(exp)
        if et in ('fint', 'flong'):
            # runtime integer exponent: call a helper that replicates
            # libgfortran's pow_*_i4 squaring (cpow/pow would differ in ULPs)
            if bt == 'fcomplex':
                return f'fmm_cpowi({be}, {ee})'
            if bt == 'double':
                return f'fmm_dpowi({be}, {ee})'
            if bt == 'float':
                return f'fmm_fpowi({be}, {ee})'
            if bt == 'flong':
                return f'fmm_lpowi({be}, {ee})'
            return f'fmm_ipowi({be}, {ee})'
        if bt == 'fcomplex':
            return f'cpow({be}, (double)({ee}))'
        if bt == 'float':
            return f'powf({be}, (float)({ee}))'
        return f'pow({be}, (double)({ee}))'

    def _int_lit_value(self, node):
        k = cls(node)
        if k == 'Int_Literal_Constant':
            return int(str(node.children[0]))
        if k == 'Signed_Int_Literal_Constant':
            return int(self._num_lit(node))
        if k == 'Parenthesis':
            return self._int_lit_value(node.children[1])
        return None

    def _pow_by_squaring(self, base, n, bt):
        if n == 0:
            return '1.0' if bt != 'fint' else '1'
        b = f'({self.expr(base)})'
        pow_expr = None
        sq = b                       # holds b^(2^k), grown by squaring
        while n > 0:
            if n & 1:
                pow_expr = sq if pow_expr is None else f'{pow_expr} * {sq}'
            n >>= 1
            if n:
                sq = f'({sq} * {sq})'
        return pow_expr

    def _is_neg_one(self, node):
        if cls(node) == 'Parenthesis':
            return self._is_neg_one(node.children[1])
        if cls(node) == 'Level_2_Unary_Expr':
            op, operand = node.children
            return op == '-' and self.expr(operand) == '1'
        return False

    def struct_ctor(self, node):
        # fparser parses an unrecognized call foo(...) used as a value as a
        # derived-type constructor. It is really one of: a complex constructor,
        # a stripped timing call, or an ordinary (user/cross-file) function.
        name = str(node.children[0]).lower()
        comp = node.children[1]
        args = list(comp.children) if comp is not None else []
        if name in ('second', 'omp_get_wtime', 'etime'):
            return '0.0'                       # timing -> deterministic zero
        if name in ('dcmplx', 'cmplx'):
            ce = [self.expr(a) for a in args]
            return (f'(({ce[0]}) + ({ce[1]}) * I)' if len(ce) == 2
                    else f'({ce[0]})')
        # not a complex constructor: an ordinary function call mislabeled by
        # fparser (e.g. dpoly(2d0) where dpoly is a same-file function).
        return self.emit_call_expr(name, args)

    def _num_lit(self, node):
        # works for (signed) real/int literals via the rendered token
        txt = str(node).replace(' ', '')
        if '_' in txt:                      # strip kind suffix _8 / _wp
            txt = txt.split('_', 1)[0]
        return txt.replace('D', 'e').replace('d', 'e').replace('E', 'e')

    def _real_prec(self, node):
        # Fortran real-literal precision: a 'd' exponent or kind 8 is double;
        # otherwise (plain or 'e' exponent, default real) it is single.
        t = str(node).upper().replace(' ', '')
        if '_' in t:
            return 'float' if t.split('_', 1)[1] == '4' else 'double'
        return 'double' if 'D' in t else 'float'

    def name_value(self, nm):
        sub = self._sf_subst.get(nm.lower())
        if sub is None:                     # statement-function dummy
            sub = self._assoc.get(nm.lower())   # or ASSOCIATE name
        if sub is not None:
            return sub
        s = self.s.get(nm)
        if s is None:
            return cname(nm)
        if s.is_dummy and not s.is_array:
            return f'(*{cname(nm)})'
        return cname(nm)

    def _inline_stmt_func(self, nm, args):
        params, body = self.s.stmt_funcs[nm.lower()]
        actuals = [f'({self.expr(a)})' for a in args]   # in the current context
        saved = self._sf_subst
        self._sf_subst = dict(zip(params, actuals))
        try:
            return '(' + self.expr(body) + ')'
        finally:
            self._sf_subst = saved

    def part_ref(self, node):
        nm = str(node.children[0])
        s = self.s.get(nm)
        subs = node.children[1]
        args = list(subs.children) if subs is not None else []
        if s is not None and s.is_array:
            return self.array_index(nm, s, args)
        # otherwise a function call written with ()
        return self.emit_call_expr(nm, args)

    def array_index(self, nm, s, args):
        return self._index_lvalue(nm, s, [self.expr(a) for a in args])

    def _dimlo(self, s, k):
        """Lower bound of dim k as C text. Prefers the value captured at
        ALLOCATE (s.dim_c) over re-evaluating the (possibly reassigned) bound."""
        if s.dim_c is not None:
            return s.dim_c[k][0]
        return self._lo(s.dims[k][0])

    def _dimext(self, s, k):
        """Extent (leading dimension) of dim k as C text, preferring the value
        captured at ALLOCATE (s.dim_c)."""
        if s.dim_c is not None:
            return s.dim_c[k][1]
        return self._extent(s.dims[k])

    def _index_lvalue(self, nm, s, idxs):
        """The C lvalue base[offset] for already-emitted index expressions."""
        nm = cname(nm)
        if len(idxs) == 1:
            lo = self._dimlo(s, 0)
            off = self._sub_lo(idxs[0], lo)
            return f'{nm}[{off}]'
        # 1-based 2-/3-D arrays: use the house-style FA2/FA3 macros
        if all(self._dimlo(s, k) == '1' for k in range(len(s.dims))):
            if len(idxs) == 2:
                ld1 = self._dimext(s, 0)
                return f'{nm}[FA2({idxs[0]}, {idxs[1]}, {ld1})]'
            if len(idxs) == 3:
                ld1 = self._dimext(s, 0)
                ld2 = self._dimext(s, 1)
                return f'{nm}[FA3({idxs[0]}, {idxs[1]}, {idxs[2]}, {ld1}, {ld2})]'
        # general N-D column-major offset (e.g. 0-based carray(0:ldc,0:ldc))
        return f'{nm}[{self._colmajor_offset(s, idxs)}]'

    def _colmajor_offset(self, s, idxs):
        parts = []
        stride = None             # product of extents of dims already seen
        n = len(s.dims)
        for k in range(n):
            lo = self._dimlo(s, k)
            term = self._sub_lo(idxs[k], lo)
            if k == 0:
                if term != '0':
                    parts.append(term)
            elif term != '0':
                parts.append(f'({term}) * ({stride})')
            # extent of the trailing (assumed-size *) dim is never needed
            if k + 1 < n:
                ext = self._dimext(s, k)
                stride = ext if stride is None else f'({stride}) * ({ext})'
        return ' + '.join(parts) if parts else '0'

    def _lo(self, lo_expr):
        if lo_expr is None:
            return '1'
        return self.expr(lo_expr)

    def _extent(self, dim):
        lo, hi = dim
        hs = self.expr(hi)
        los = self._lo(lo)
        if los == '1':
            return hs
        if los == '0':
            return f'({hs}) + 1'
        return f'({hs}) - ({los}) + 1'

    def _sub_lo(self, idx, lo):
        if lo == '1':
            return f'{idx} - 1'
        if lo == '0':
            return idx
        return f'({idx}) - ({lo})'

    def func_ref(self, node):
        name_node = node.children[0]
        nm = str(name_node)
        argspec = node.children[1]
        args = list(argspec.children) if argspec is not None else []
        return self.emit_call_expr(nm, args)

    def emit_call_expr(self, nm, args):
        up = nm.upper()
        # a statement function shadows any intrinsic of the same name: inline it
        if nm.lower() in self.s.stmt_funcs:
            return self._inline_stmt_func(nm, args)
        # reductions take an array *section*; dispatch before evaluating args
        if up in ('MAXVAL', 'MINVAL', 'SUM', 'PRODUCT'):
            return self._reduce(up, args[0])
        if up in ('COUNT', 'ALL', 'ANY'):
            return self._mask_reduce(up, args[0])
        if up == 'DOT_PRODUCT':
            return self._dot_product(args)
        if up in ('MAXLOC', 'MINLOC'):
            return self._extreme_loc(up, args[0])
        if up == 'LEN':                      # character length / len_trim
            return self._char_operand(args[0])[1]
        if up == 'LEN_TRIM':
            ptr, ln = self._char_operand(args[0])
            return f'fmm_lentrim({ptr}, {ln})'
        if up in ('ICHAR', 'IACHAR'):
            ptr, ln = self._char_operand(args[0])
            return f'(fint)(unsigned char)({ptr}[0])'
        if up == 'INDEX':
            p1, l1 = self._char_operand(args[0])
            p2, l2 = self._char_operand(args[1])
            return f'fmm_index({p1}, {l1}, {p2}, {l2})'
        # CHARACTER actuals (literals/strings) have no scalar C value -- they
        # pass as pointer + hidden length, handled by _call_args on the generic
        # user-function path below (and by the char-aware intrinsics above). So
        # don't force them through expr() here, which has no char-scalar form.
        a = [None if self.expr_ctype(x) == 'char' else self.expr(x)
             for x in args]
        t = [self.expr_ctype(x) for x in args]
        cx = t[0] == 'fcomplex' if t else False
        # 'f' picks the single-precision libm variant (sqrtf, sinf, ...) when
        # every real argument is float, so the C math matches gfortran's.
        fp = 'f' if (t and all(x == 'float' for x in t)) else ''

        if up in ('DCMPLX', 'CMPLX'):
            return f'(({a[0]}) + ({a[1]}) * I)' if len(a) == 2 else f'({a[0]})'
        if up in ('DIMAG', 'AIMAG', 'IMAG'):
            return f'cimag({a[0]})'
        if up == 'DREAL':
            return f'creal({a[0]})'
        if up == 'DBLE':
            return f'creal({a[0]})' if cx else f'(double)({a[0]})'
        if up == 'REAL':                       # default real -> single
            return f'(float)creal({a[0]})' if cx else f'(float)({a[0]})'
        if up in ('DCONJG', 'CONJG'):
            return f'conj({a[0]})'
        if up == 'CDABS':
            return f'cabs({a[0]})'
        if up == 'ABS':
            if cx:
                return f'cabs({a[0]})'
            if t and t[0] == 'flong':
                return f'fmm_labs({a[0]})'
            if t and t[0] == 'fint':
                return f'fmm_iabs({a[0]})'
            return f'fabs{fp}({a[0]})'
        if up == 'DABS':
            return f'fabs({a[0]})'
        if up == 'IABS':
            return (f'fmm_labs({a[0]})' if t and t[0] == 'flong'
                    else f'fmm_iabs({a[0]})')
        if up in ('CDLOG',):
            return f'clog({a[0]})'
        if up == 'LOG':
            return f'clog({a[0]})' if cx else f'log{fp}({a[0]})'
        if up == 'DLOG':
            return f'log({a[0]})'
        if up in ('CDEXP',):
            return f'cexp({a[0]})'
        if up == 'EXP':
            return f'cexp({a[0]})' if cx else f'exp{fp}({a[0]})'
        if up == 'DEXP':
            return f'exp({a[0]})'
        if up in ('CDSQRT',):
            return f'csqrt({a[0]})'
        if up == 'SQRT':
            return f'csqrt({a[0]})' if cx else f'sqrt{fp}({a[0]})'
        if up == 'DSQRT':
            return f'sqrt({a[0]})'
        if up in ('INT', 'IDINT'):
            return f'(fint)({a[0]})'
        if up == 'INT8':                       # convert to INTEGER*8
            return f'(flong)({a[0]})'
        if up in ('INT4', 'INT2', 'INT1'):     # convert to narrower INTEGER kinds
            return f'(fint)({a[0]})'
        if up in ('NINT', 'IDNINT'):
            return f'(fint)round({a[0]})'
        if up == 'FLOAT':                      # -> default real (single)
            return f'(float)({a[0]})'
        if up == 'DFLOAT':
            return f'(double)({a[0]})'
        if up == 'ATAN':
            return f'atan{fp}({a[0]})'
        if up == 'DATAN':
            return f'atan({a[0]})'
        if up == 'ATAN2':
            return f'atan2{fp}({a[0]}, {a[1]})'
        if up == 'DATAN2':
            return f'atan2({a[0]}, {a[1]})'
        if up == 'SIN':
            return f'sin{fp}({a[0]})'
        if up == 'DSIN':
            return f'sin({a[0]})'
        if up == 'COS':
            return f'cos{fp}({a[0]})'
        if up == 'DCOS':
            return f'cos({a[0]})'
        if up == 'TAN':
            return f'tan{fp}({a[0]})'
        if up == 'DTAN':
            return f'tan({a[0]})'
        if up == 'ASIN':
            return f'asin{fp}({a[0]})'
        if up == 'DASIN':
            return f'asin({a[0]})'
        if up == 'ACOS':
            return f'acos{fp}({a[0]})'
        if up == 'DACOS':
            return f'acos({a[0]})'
        if up == 'SINH':
            return f'sinh{fp}({a[0]})'
        if up == 'DSINH':
            return f'sinh({a[0]})'
        if up == 'COSH':
            return f'cosh{fp}({a[0]})'
        if up == 'DCOSH':
            return f'cosh({a[0]})'
        if up == 'TANH':
            return f'tanh{fp}({a[0]})'
        if up == 'DTANH':
            return f'tanh({a[0]})'
        if up == 'LOG10':
            return f'log10{fp}({a[0]})'
        if up in ('DLOG10', 'ALOG10'):
            return f'log10({a[0]})'
        if up == 'FLOOR':
            return f'(fint)floor({a[0]})'
        if up == 'CEILING':
            return f'(fint)ceil({a[0]})'
        if up in ('IAND',):
            return f'(({a[0]}) & ({a[1]}))'
        if up in ('IOR',):
            return f'(({a[0]}) | ({a[1]}))'
        if up in ('IEOR',):
            return f'(({a[0]}) ^ ({a[1]}))'
        if up == 'NOT':
            return f'(~({a[0]}))'
        if up == 'ISHFT':
            return (f'fmm_lshft({a[0]}, {a[1]})' if t and t[0] == 'flong'
                    else f'fmm_ishft({a[0]}, {a[1]})')
        if up in ('IBSET', 'IBCLR'):
            one = '(flong)1' if t and t[0] == 'flong' else '1'
            shifted = f'({one} << ({a[1]}))'
            return (f'(({a[0]}) | {shifted})' if up == 'IBSET'
                    else f'(({a[0]}) & ~{shifted})')
        if up == 'BTEST':
            return f'(((({a[0]}) >> ({a[1]})) & 1) != 0)'
        if up == 'IBITS':
            one = '(flong)1' if t and t[0] == 'flong' else '1'
            return f'((({a[0]}) >> ({a[1]})) & (({one} << ({a[2]})) - 1))'
        if up == 'ISHFTC':
            sz = a[2] if len(a) > 2 else ('64' if t and t[0] == 'flong'
                                          else '32')
            return (f'fmm_lshftc({a[0]}, {a[1]}, {sz})' if t and t[0] == 'flong'
                    else f'fmm_ishftc({a[0]}, {a[1]}, {sz})')
        if up == 'DPROD':
            return f'((double)({a[0]}) * (double)({a[1]}))'
        if up == 'MERGE':
            return f'(({a[2]}) ? ({a[0]}) : ({a[1]}))'
        if up in ('DIM', 'DDIM', 'IDIM'):
            return f'(({a[0]}) > ({a[1]}) ? ({a[0]}) - ({a[1]}) : 0)'
        # numeric inquiry: the argument's type/kind matters, not its value
        if up == 'EPSILON':
            return 'FLT_EPSILON' if t[0] == 'float' else 'DBL_EPSILON'
        if up == 'TINY':
            return 'FLT_MIN' if t[0] == 'float' else 'DBL_MIN'
        if up == 'HUGE':
            return {'float': 'FLT_MAX', 'double': 'DBL_MAX',
                    'flong': 'INT64_MAX', 'fint': 'INT32_MAX'}[t[0]]
        if up == 'RADIX':                      # model base: 2 for IEEE reals
            return 'FLT_RADIX'
        if up == 'DIGITS':                     # significant base-RADIX digits
            return {'float': 'FLT_MANT_DIG', 'double': 'DBL_MANT_DIG',
                    'fint': '31', 'flong': '63'}[t[0]]
        if up == 'MINEXPONENT':
            return 'FLT_MIN_EXP' if t[0] == 'float' else 'DBL_MIN_EXP'
        if up == 'MAXEXPONENT':
            return 'FLT_MAX_EXP' if t[0] == 'float' else 'DBL_MAX_EXP'
        if up == 'BIT_SIZE':
            return '64' if t[0] == 'flong' else '32'
        if up == 'KIND':
            return {'fint': '4', 'flong': '8', 'float': '4',
                    'double': '8', 'fcomplex': '8'}[t[0]]
        if up == 'SIGN':
            if t and t[0] == 'flong':
                return f'fmm_lsign({a[0]}, {a[1]})'
            if t and t[0] == 'fint':
                return f'fmm_isign({a[0]}, {a[1]})'
            return f'copysign{fp}({a[0]}, {a[1]})'
        if up == 'DSIGN':
            return f'copysign({a[0]}, {a[1]})'
        if up == 'ISIGN':
            return (f'fmm_lsign({a[0]}, {a[1]})' if t and t[0] == 'flong'
                    else f'fmm_isign({a[0]}, {a[1]})')
        if up == 'AINT':
            return f'trunc{fp}({a[0]})'
        if up == 'DINT':
            return f'trunc({a[0]})'
        if up == 'ANINT':
            return f'round{fp}({a[0]})'
        if up == 'DNINT':
            return f'round({a[0]})'
        if up in ('MAX', 'MAX0', 'MAX1', 'DMAX1', 'AMAX1'):
            return self._fold_minmax(a, '>')
        if up in ('MIN', 'MIN0', 'MIN1', 'DMIN1', 'AMIN1'):
            return self._fold_minmax(a, '<')
        if up == 'DMOD':
            return f'fmod({a[0]}, {a[1]})'
        if up in ('MOD',):
            if t and t[0] == 'double':
                return f'fmod({a[0]}, {a[1]})'
            if t and t[0] == 'float':
                return f'fmodf({a[0]}, {a[1]})'
            return f'(({a[0]}) % ({a[1]}))'
        if up == 'MODULO':
            # result has the sign of the divisor (unlike MOD / C's %)
            if t and t[0] == 'double':
                return f'(({a[0]}) - floor(({a[0]}) / ({a[1]})) * ({a[1]}))'
            if t and t[0] == 'float':
                return f'(({a[0]}) - floorf(({a[0]}) / ({a[1]})) * ({a[1]}))'
            return f'(((({a[0]}) % ({a[1]})) + ({a[1]})) % ({a[1]}))'
        # otherwise: a user (cross-file or same-file) function used as a value
        rt = self.func_ctype(nm, args)
        return f'{self.call_name(nm, rt)}({self._call_args(nm, args)})'

    def _whole_hi(self, up, s):
        if s.dims[0][1] is None:
            raise Unsupported(f'{up} over array of unknown extent')
        return self.expr(s.dims[0][1])

    def _fold_minmax(self, a, op):
        acc = a[0]
        for x in a[1:]:
            acc = f'(({acc}) {op} ({x}) ? ({acc}) : ({x}))'
        return acc

    def _reduce(self, up, arg):
        # maxval/minval/sum over a 1-D array section base(lo:hi) or a whole
        # 1-D array base (in which case the bounds come from its declaration).
        k = cls(arg)
        if k == 'Part_Ref':
            nm = str(arg.children[0])
            s = self.s.get(nm)
            if s is None or not s.is_array:
                raise Unsupported(f'{up} of non-array {arg}')
            trip = arg.children[1].children[0]
            if cls(trip) != 'Subscript_Triplet':
                raise Unsupported(f'{up} of non-triplet {arg}')
            lo = (self.expr(trip.children[0]) if trip.children[0] is not None
                  else self._lo(s.dims[0][0]))
            hi = (self.expr(trip.children[1]) if trip.children[1] is not None
                  else self._whole_hi(up, s))
            lob = self._lo(s.dims[0][0])
        elif k == 'Name':
            # whole array of any rank: reduce flat over its contiguous storage
            nm = str(arg)
            s = self.s.get(nm)
            if s is None or not s.is_array:
                raise Unsupported(f'{up} of non-array {arg}')
            lo, lob = '1', '1'
            hi = self._array_total_size(s)
        else:
            raise Unsupported(f'{up} of {arg}')
        ct = s.ctype

        def elem(idx):
            return f'{cname(nm)}[{self._sub_lo(idx, lob)}]'

        acc = self._new_tmp('acc')
        iv = self._new_tmp('i')
        self.pre.append(f'fint {iv};')
        self.pre.append(f'{ct} {acc} = {elem(lo)};')
        if up in ('SUM', 'PRODUCT'):
            op = '+=' if up == 'SUM' else '*=';
            self.pre.append(
                f'for ({iv} = ({lo}) + 1; {iv} <= ({hi}); {iv}++) '
                f'{acc} {op} {elem(iv)};')
        else:
            op = '>' if up == 'MAXVAL' else '<'
            self.pre.append(
                f'for ({iv} = ({lo}) + 1; {iv} <= ({hi}); {iv}++) '
                f'if ({elem(iv)} {op} {acc}) {acc} = {elem(iv)};')
        return acc

    def _whole_1d(self, node):
        """(name, sym) for a whole 1-D array argument; loud otherwise."""
        if cls(node) != 'Name':
            raise Unsupported(f'expected a whole array, got {node}')
        s = self.s.get(str(node))
        if s is None or not s.is_array:
            raise Unsupported(f'{node} is not an array')
        return str(node), s

    def _dot_product(self, args):
        na, sa = self._whole_1d(args[0])
        nb, sb = self._whole_1d(args[1])
        size = self._array_total_size(sa)
        rct = _promote(sa.ctype, sb.ctype)
        acc = self._new_tmp('acc')
        iv = self._new_tmp('i')
        ea = f'{cname(na)}[{iv}]'
        if sa.ctype == 'fcomplex':          # dot_product conjugates arg 1
            ea = f'conj({ea})'
        self.pre.append(f'fint {iv};')
        self.pre.append(f'{rct} {acc} = {"0.0" if rct != "fint" else "0"};')
        self.pre.append(f'for ({iv} = 0; {iv} < {size}; {iv}++) '
                        f'{acc} += {ea} * {cname(nb)}[{iv}];')
        return acc

    def _mask_size(self, node):
        for n in walk(node, f03.Name):
            s = self.s.get(str(n))
            if s is not None and s.is_array:
                return self._array_total_size(s)
        raise Unsupported('array mask has no array operand')

    def _mask_reduce(self, up, mask):
        size = self._mask_size(mask)
        iv = self._new_tmp('i')
        acc = self._new_tmp('acc')
        m = self._array_elem_expr(mask, iv)
        self.pre.append(f'fint {iv};')
        init = '0' if up in ('COUNT', 'ANY') else '1'
        self.pre.append(f'fint {acc} = {init};')
        if up == 'COUNT':
            body = f'if ({m}) {acc}++;'
        elif up == 'ANY':
            body = f'if ({m}) {acc} = 1;'
        else:                                # ALL
            body = f'if (!({m})) {acc} = 0;'
        self.pre.append(f'for ({iv} = 0; {iv} < {size}; {iv}++) {body}')
        return acc

    def _extreme_loc(self, up, arg):
        nm, s = self._whole_1d(arg)
        size = self._array_total_size(s)
        loc = self._new_tmp('loc')
        best = self._new_tmp('acc')
        iv = self._new_tmp('i')
        op = '>' if up == 'MAXLOC' else '<'
        self.pre.append(f'fint {iv};')
        self.pre.append(f'fint {loc} = 1;')
        self.pre.append(f'{s.ctype} {best} = {cname(nm)}[0];')
        self.pre.append(
            f'for ({iv} = 1; {iv} < {size}; {iv}++) '
            f'if ({cname(nm)}[{iv}] {op} {best}) {{ {best} = {cname(nm)}[{iv}]; '
            f'{loc} = {iv} + 1; }}')
        return loc

    # -- type inference (enough to choose real vs complex intrinsics) -------

    def expr_ctype(self, node):
        k = cls(node)
        if k in ('Int_Literal_Constant', 'Signed_Int_Literal_Constant'):
            return 'fint'
        if k == 'Logical_Literal_Constant':
            return 'fint'
        if k == 'Char_Literal_Constant':
            return 'char'
        if k in ('Real_Literal_Constant', 'Signed_Real_Literal_Constant'):
            return self._real_prec(node)
        if k == 'Complex_Literal_Constant':
            return 'fcomplex'
        if k == 'Parenthesis':
            return self.expr_ctype(node.children[1])
        if k == 'Level_2_Unary_Expr':
            return self.expr_ctype(node.children[1])
        if k == 'And_Operand' and isinstance(node.children[0], str):
            return 'fint'                       # unary .NOT.
        if k == 'Structure_Constructor':
            nm = str(node.children[0]).lower()
            if nm in ('dcmplx', 'cmplx'):
                return 'fcomplex'
            if nm in ('second', 'omp_get_wtime', 'etime'):
                return 'double'
            comp = node.children[1]
            cargs = list(comp.children) if comp is not None else []
            return self.func_ctype(nm, cargs)
        if k == 'Name':
            at = self._assoc_ctype.get(str(node).lower())
            if at is not None:                  # ASSOCIATE name
                return at
            s = self.s.get(str(node))
            return s.ctype if (s and s.ctype) else 'double'
        if k == 'Part_Ref':
            nm = str(node.children[0])
            s = self.s.get(nm)
            if s is not None and s.ctype == 'char' and not s.is_array:
                return 'char'              # substring of a character scalar
            if s is not None and s.is_array:
                return s.ctype
            return self.func_ctype(nm, list(node.children[1].children))
        if k in ('Intrinsic_Function_Reference', 'Function_Reference'):
            nm = str(node.children[0])
            argspec = node.children[1]
            fargs = list(argspec.children) if argspec is not None else []
            return self.func_ctype(nm, fargs)
        items = node.children
        if len(items) == 3 and isinstance(items[1], str):
            op = items[1]
            if op in REL_OPS or op in LOG_OPS:
                return 'fint'
            return _promote(self.expr_ctype(items[0]),
                            self.expr_ctype(items[2]))
        return 'double'

    def func_ctype(self, nm, args):
        up = nm.upper()
        if up in INTRIN_COMPLEX_RESULT:
            return 'fcomplex'
        if up in INTRIN_DOUBLE_RESULT:
            return 'double'
        if up in INTRIN_SINGLE_RESULT:
            return 'float'
        if up == 'INT8':                    # convert to INTEGER*8
            return 'flong'
        if up in ('INT4', 'INT2', 'INT1'):  # convert to narrower INTEGER kinds
            return 'fint'
        if up in INTRIN_INT_RESULT:
            return 'fint'
        if up in ('MAX0', 'MAX1', 'MIN0', 'MIN1'):
            return 'fint'
        if up in ('DMAX1', 'DMIN1'):
            return 'double'
        if up in ('AMAX1', 'AMIN1'):
            return 'float'
        if up == 'MERGE':           # result follows the two value arguments
            return _promote(self.expr_ctype(args[0]), self.expr_ctype(args[1]))
        if up in INTRIN_FOLLOW_ARG or up == 'DOT_PRODUCT':
            # result kind follows the promoted kind of the arguments (for an
            # array argument that is its element kind)
            t = 'fint'
            for ar in args:
                t = _promote(t, self.expr_ctype(ar))
            return t
        # unknown (user) function: result type follows the Fortran rule that
        # the function name carries its own (declared or implicit) type
        s = self.s.get(nm)
        if s is not None and s.ctype and not s.is_array:
            return s.ctype
        return self.s.implicit_ctype(nm)

    # -- call targets (subroutine CALL or by-ref function) ------------------

    def call_name(self, nm, rettype='void'):
        nm = nm.lower()
        if nm in self.s.file_procs:
            return f'FNAME({nm})'
        # a non-void return type (a function) wins over a void (subroutine)
        if rettype != 'void' or nm not in self.xcalls:
            self.xcalls[nm] = rettype
        return f'{nm}_'

    def actual_arg(self, node):
        """Return C text for a by-reference actual argument (an address)."""
        k = cls(node)
        if k == 'Name':
            nm = str(node)
            s = self.s.get(nm)
            if s is not None and (s.is_array or s.is_alloc):
                return cname(nm)               # array/pointer: pass base
            if s is not None and s.is_dummy:
                return cname(nm)               # scalar dummy: already a pointer
            return f'&{cname(nm)}'             # scalar local / parameter
        if k == 'Part_Ref':
            nm = str(node.children[0])
            s = self.s.get(nm)
            # Only an actual array is addressable as &arr[...]; a Part_Ref whose
            # base is not an array is a function call written f(x) (e.g. an
            # intrinsic conversion int8(3)), whose result is an rvalue -- it
            # must go through the temp path below, not &f(x).
            if s is not None and s.is_array:
                subs = list(node.children[1].children)
                if any(cls(ss) == 'Subscript_Triplet' for ss in subs):
                    # array section a(lo:hi, ...): pass the address of its first
                    # element (Fortran sequence association)
                    starts = []
                    for d, ss in enumerate(subs):
                        if cls(ss) == 'Subscript_Triplet':
                            starts.append(self.expr(ss.children[0])
                                          if ss.children[0] is not None
                                          else self._lo(s.dims[d][0]))
                        else:
                            starts.append(self.expr(ss))
                    return '&' + self._index_lvalue(nm, s, starts)
                # array element arg: &arr[...]
                return '&' + self.part_ref(node)
        # literal, expression, or function-call result: materialize a temp of
        # the inferred type and pass its address
        ctype = self.expr_ctype(node)
        t = self._new_tmp()
        self.pre.append(f'{ctype} {t} = {self.expr(node)};')
        return f'&{t}'

    def _new_tmp(self, prefix='arg'):
        # the `__`/`_[A-Z]` namespace is reserved for the implementation (e.g.
        # GCC predefines `__k8` for the x86-64 baseline), so use `f2c_`.
        self.tmp_n += 1
        return f'f2c_{prefix}{self.tmp_n}'

    # -- statements ---------------------------------------------------------

    def stmt(self, node, out, indent):
        saved = self.pre               # scope `pre` to this statement so a
        self.pre = []                  # nested stmt() can't leak its prelude
        lines = self._stmt(node, indent)
        pad = '    ' * indent
        for p in self.pre:
            out.append(pad + p)
        out.extend(lines)
        self.pre = saved

    def _label_prefix(self, node):
        lbl = getattr(getattr(node, 'item', None), 'label', None)
        return f'L{lbl}: ' if lbl is not None else ''

    def _stmt(self, node, indent):
        pad = '    ' * indent
        k = cls(node)
        lp = self._label_prefix(node)

        if k == 'Assignment_Stmt':
            lhs0 = node.children[0]
            if (cls(lhs0) == 'Part_Ref'
                    and str(lhs0.children[0]).lower() in self.s.stmt_funcs):
                return []                   # stmt-function def: inlined at uses
            ls = (self.s.get(str(lhs0.children[0]) if cls(lhs0) == 'Part_Ref'
                             else str(lhs0)) if cls(lhs0) in ('Name', 'Part_Ref')
                  else None)
            if ls is not None and ls.ctype == 'char':
                if cls(lhs0) == 'Part_Ref':
                    raise Unsupported('character substring assignment')
                return [pad + lp + self._char_assign(lhs0, node.children[2])]
            if self._is_whole_array(lhs0):
                return self._array_assign(node, indent)
            lhs = self.lhs(node.children[0])
            rhs = self.expr(node.children[2])
            return [pad + lp + f'{lhs} = {rhs};']

        if k == 'Continue_Stmt':
            return [pad + lp + ';']

        if k == 'Format_Stmt':
            # A FORMAT label is referenced only as a format specifier by
            # WRITE/PRINT (which are themselves translated or stripped); it is
            # never a GOTO target, so the statement has no C form -- drop it.
            return []

        if k == 'Entry_Stmt':
            # ENTRY marks an alternate entry point. Each entry is emitted as its
            # own C function (see emit_proc / entry_scopes); within the body it
            # is just a boundary marker, so emit nothing here.
            return []

        if k == 'Print_Stmt':
            # In a main program a PRINT is the program's output, so translate it
            # to printf. In a subroutine/function it is a diagnostic message
            # (the fmm2d convention); strip it as before.
            if self.s.is_main:
                return [pad + lp + self.print_stmt(node)]
            return [pad + lp + ';'] if lp else []

        if k == 'Write_Stmt':
            # WRITE(*,fmt) / WRITE(6,fmt) to stdout behaves like PRINT here.
            if self.s.is_main:
                line = self.write_stmt(node)
                if line is not None:
                    return [pad + lp + line]
            return [pad + lp + ';'] if lp else []

        if k == 'Stop_Stmt':
            # STOP halts the program. Map to exit(code): an integer stop-code is
            # honored; a string stop-message is a diagnostic (dropped, like the
            # PRINT/WRITE convention) and terminates with 0. fparser models the
            # code as Stop_Stmt('STOP', Stop_Code(...) | None).
            code = '0'
            sc = node.children[1] if len(node.children) > 1 else None
            if sc is not None and re.fullmatch(r'[+-]?\d+', str(sc).strip()):
                code = str(sc).strip()
            return [pad + lp + f'exit({code});']

        if k == 'Exit_Stmt':
            return [pad + lp + 'break;']

        if k == 'Cycle_Stmt':
            return [pad + lp + 'continue;']

        if k == 'Return_Stmt':
            # Fortran auto-deallocates local allocatables on return; emit the
            # matching free()s before leaving (free(NULL) is a safe no-op for
            # allocatables not currently allocated).
            ret = (f'return {cname(self.s.result_name)};'
                   if self.s.is_function else 'return;')
            stmts = self._alloc_frees() + [ret]
            return [pad + lp + stmts[0]] + [pad + s for s in stmts[1:]]

        if k == 'Goto_Stmt':
            tgt = str(walk(node, f03.Label)[0]) if walk(node, f03.Label) else None
            if tgt is None:
                tgt = str(node).split()[-1]
            return [pad + lp + f'goto L{tgt};']

        if k == 'Call_Stmt':
            callee = str(node.children[0]).lower()
            # strip logging (prini/prinf/prin2/...) entirely
            if callee.startswith('prin'):
                return [pad + lp + ';'] if lp else []
            # timing -> deterministic zero (diff tests don't compare timeinfo)
            if callee in ('cpu_time', 'second'):
                arg = list(node.children[1].children)[0]
                return [pad + lp + f'{self.lhs(arg)} = 0.0;']
            if callee == 'mvbits':
                return [pad + lp + self._mvbits(list(node.children[1].children))]
            return [pad + lp + self.call_stmt(node)]

        if k == 'Allocate_Stmt':
            mallocs = self.alloc_stmt(node)
            return [pad + lp + mallocs[0]] + [pad + m for m in mallocs[1:]]

        if k == 'Deallocate_Stmt':
            # free + NULL so the implicit end-of-scope free (see emit_proc /
            # Return_Stmt) does not double-free an explicitly deallocated array.
            out = []
            for nm in walk(node, f03.Name):
                cn = cname(str(nm))
                out.append(f'free({cn});')
                out.append(f'{cn} = NULL;')
            return [pad + lp + out[0]] + [pad + o for o in out[1:]]

        if k == 'If_Stmt':
            cond = self.expr(node.children[0])
            inner = self._stmt(node.children[1], 0)
            if not inner:                       # inner stripped (e.g. a print)
                return [pad + lp + f'if ({cond}) {{}}']
            if len(inner) == 1:
                return [pad + lp + f'if ({cond}) ' + inner[0].strip()]
            # multi-line inner (e.g. `return` that also frees allocatables):
            # a single-statement `if` would only guard the first line, so wrap
            # the whole thing in a block.
            return ([pad + lp + f'if ({cond}) {{']
                    + ['    ' + pad + s.strip() for s in inner]
                    + [pad + '}'])

        if k == 'Arithmetic_If_Stmt':
            # IF (e) n1, n2, n3  ->  branch on sign of e (<0, ==0, >0)
            e = self.expr(node.children[0])
            labs = [str(node.children[i]) for i in (1, 2, 3)]
            return [pad + lp + f'if (({e}) < 0) goto L{labs[0]}; '
                    f'else if (({e}) == 0) goto L{labs[1]}; '
                    f'else goto L{labs[2]};']

        if k == 'If_Construct':
            return self.if_construct(node, indent)

        if k == 'Where_Stmt':
            return self.where_stmt(node, indent)

        if k == 'Where_Construct':
            return self.where_construct(node, indent)

        if k == 'Associate_Construct':
            return self.associate_construct(node, indent)

        if k == 'Forall_Stmt':
            return self.forall(node, [node.children[1]], indent)

        if k == 'Forall_Construct':
            assigns = [c for c in node.children if cls(c) == 'Assignment_Stmt']
            if len(assigns) != sum(1 for c in node.children
                                   if cls(c) not in ('Forall_Construct_Stmt',
                                                     'End_Forall_Stmt')):
                raise Unsupported('FORALL body with non-assignment statements')
            return self.forall(node, assigns, indent)

        if k == 'Case_Construct':
            return self.case_construct(node, indent)

        if k == 'Computed_Goto_Stmt':
            labels = [str(l) for l in walk(node.children[0], f03.Label)]
            sel = self.expr(node.children[1])
            arms = ' '.join(f'case {j + 1}: goto L{lab};'
                            for j, lab in enumerate(labels))
            return [pad + lp + f'switch ({sel}) {{ {arms} }}']

        if k in ('Block_Nonlabel_Do_Construct', 'Block_Label_Do_Construct'):
            return self.do_construct(node, indent)

        raise Unsupported(f'statement {k}: {node}')

    def _alloc_frees(self):
        """free() calls for this scope's local allocatable arrays, in
        declaration order. Used to mirror Fortran's automatic deallocation of
        allocatables at every routine exit (return and fall-through)."""
        frees = []
        for nm in self.s.syms:
            sym = self.s.get(nm)
            if sym.is_alloc and not sym.is_dummy:
                frees.append(f'free({cname(nm)});')
        return frees

    def lhs(self, node):
        k = cls(node)
        if k == 'Name':
            nm = str(node)
            sub = self._assoc.get(nm.lower())   # assignment through an alias
            if sub is not None:
                return sub
            s = self.s.get(nm)
            if s is not None and s.is_dummy and not s.is_array:
                return f'*{cname(nm)}'
            return cname(nm)
        if k == 'Part_Ref':
            return self.part_ref(node)
        raise Unsupported(f'lhs {k}')

    # -- character strings --------------------------------------------------

    def _char_operand(self, node):
        """(c_pointer_expr, c_length_expr) for a character value: a literal, a
        scalar variable, a substring, or a char-valued intrinsic
        (trim/char/achar/adjustl/adjustr)."""
        k = cls(node)
        if k == 'Char_Literal_Constant':
            text = _unquote_fortran(str(node))
            lit = '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
            return lit, str(len(text))
        if k == 'Name':
            s = self.s.get(str(node))
            return cname(str(node)), str(s.char_len)
        sb = (self.s.get(str(node.children[0])) if k == 'Part_Ref' else None)
        if k == 'Part_Ref' and sb is not None and sb.ctype == 'char':
            nm = cname(str(node.children[0]))
            subs = list(node.children[1].children)
            if len(subs) == 1 and cls(subs[0]) == 'Subscript_Triplet':
                lo, hi, _ = subs[0].children
                lo_e = self.expr(lo) if lo is not None else '1'
                hi_e = self.expr(hi) if hi is not None else str(sb.char_len)
                return (f'({nm} + (({lo_e}) - 1))',
                        f'(({hi_e}) - ({lo_e}) + 1)')
            if len(subs) == 1:               # single character var(i)
                return f'({nm} + (({self.expr(subs[0])}) - 1))', '1'
        fname, fargs = self._callish(node)
        if fname == 'trim':
            p, ln = self._char_operand(fargs[0])
            return p, f'fmm_lentrim({p}, {ln})'
        if fname in ('char', 'achar'):
            tmp = self._new_tmp('ch')
            self.pre.append(f'char {tmp} = (char)({self.expr(fargs[0])});')
            return f'(&{tmp})', '1'
        if fname in ('adjustl', 'adjustr'):
            p, ln = self._char_operand(fargs[0])
            if not ln.isdigit():
                raise Unsupported(f'{fname} of a runtime-length string')
            tmp = self._new_tmp('adj')
            self.pre.append(f'char {tmp}[{ln}];')
            self.pre.append(f'fmm_{fname}({tmp}, {p}, {ln});')
            return tmp, ln
        # concatenation `a // b // ...` as a value (e.g. an actual argument):
        # materialize the joined string into a temp buffer and return it with
        # its total length. (Character *assignment* with // is handled
        # separately by _char_assign.)
        if cls(node) == 'Level_3_Expr' and node.children[1] == '//':
            segs = self._concat_segments(node)
            if all(ln.isdigit() for _, ln in segs):
                total = str(sum(int(ln) for _, ln in segs))
            else:
                total = ' + '.join(f'({ln})' for _, ln in segs)
            tmp = self._new_tmp('cat')
            pos = self._new_tmp('p')
            self.pre.append(f'char {tmp}[{total}];')
            self.pre.append(f'fint {pos} = 0;')
            for ptr, ln in segs:
                self.pre.append(
                    f'fmm_strcat({tmp}, {total}, &{pos}, {ptr}, {ln});')
            return tmp, total
        raise Unsupported(f'character operand {node}')

    def _concat_segments(self, node):
        if cls(node) == 'Level_3_Expr' and node.children[1] == '//':
            return (self._concat_segments(node.children[0])
                    + self._concat_segments(node.children[2]))
        return [self._char_operand(node)]

    def _char_assign(self, lhs, rhs):
        dest = cname(str(lhs))
        n = self.s.get(str(lhs)).char_len
        p = self._new_tmp('p')
        parts = [f'fint {p} = 0;']
        for ptr, ln in self._concat_segments(rhs):
            parts.append(f'fmm_strcat({dest}, {n}, &{p}, {ptr}, {ln});')
        parts.append(f'fmm_strpad({dest}, {n}, {p});')
        return '{ ' + ' '.join(parts) + ' }'

    # -- whole-array assignment (a = b*2, a = b + c, a = scalar) -------------

    def _is_whole_array(self, node):
        """A bare array name, or a(:)-style section that spans the whole array."""
        if cls(node) == 'Name':
            s = self.s.get(str(node))
            return s is not None and s.is_array
        if cls(node) == 'Part_Ref':
            s = self.s.get(str(node.children[0]))
            if s is None or not s.is_array:
                return False
            subs = node.children[1]
            sslist = list(subs.children) if subs is not None else []
            return bool(sslist) and all(
                cls(ss) == 'Subscript_Triplet' and all(c is None for c in ss.children)
                for ss in sslist)
        return False

    def _array_total_size(self, s):
        try:
            sz = 1
            for lo, hi in s.dims:
                h = const_eval_int(hi, self.s)
                l = const_eval_int(lo, self.s) if lo is not None else 1
                sz *= (h - l + 1)
            return str(sz)
        except Unsupported:
            exts = [self._extent(d) for d in s.dims]
            return ' * '.join(f'({e})' for e in exts)

    def _array_elem_expr(self, node, idx):
        """A (possibly array-valued) expression evaluated at flat element `idx`:
        array operands become base[idx], scalars stay as-is. Handles arithmetic,
        relational and logical operators (the last two for WHERE masks)."""
        if self._is_whole_array(node):
            return f'{cname(str(node.children[0]) if cls(node) == "Part_Ref" else str(node))}[{idx}]'
        k = cls(node)
        if k == 'Parenthesis':
            return '(' + self._array_elem_expr(node.children[1], idx) + ')'
        if k == 'Level_2_Unary_Expr':
            op, operand = node.children
            return f'{op}{self._array_elem_expr(operand, idx)}'
        if k == 'And_Operand' and isinstance(node.children[0], str):
            return f'!({self._array_elem_expr(node.children[1], idx)})'
        items = node.children
        if len(items) == 3 and isinstance(items[1], str):
            op = items[1]
            l = self._array_elem_expr(items[0], idx)
            r = self._array_elem_expr(items[2], idx)
            if op in ('+', '-', '*', '/'):
                return f'{l} {op} {r}'
            if op in REL_OPS:
                return f'{l} {REL_OPS[op]} {r}'
            if op in LOG_OPS:
                return f'{l} {LOG_OPS[op]} {r}'
            raise Unsupported(f'whole-array operator {op!r}')
        return self.expr(node)               # scalar leaf (literal, a(j), ...)

    def _array_assign(self, node, indent):
        pad = '    ' * indent
        lp = self._label_prefix(node)
        lhs_node, rhs_node = node.children[0], node.children[2]
        nm = str(lhs_node.children[0]) if cls(lhs_node) == 'Part_Ref' \
            else str(lhs_node)
        s = self.s.get(nm)
        if cls(rhs_node) == 'Array_Constructor':
            # a = (/ v0, v1, ... /) : one assignment per element
            vlist = rhs_node.children[1]
            vals = (list(vlist.children) if cls(vlist).endswith('_List')
                    else [vlist])
            return [pad + (lp if j == 0 else '')
                    + f'{cname(nm)}[{j}] = {self.expr(v)};'
                    for j, v in enumerate(vals)]
        fname, fargs = self._callish(rhs_node)
        if fname == 'transpose':
            return self._emit_transpose(nm, s, fargs[0], pad, lp)
        if fname == 'matmul':
            return self._emit_matmul(nm, s, fargs, pad, lp)
        if fname == 'reshape':
            return self._emit_reshape(nm, s, fargs[0], pad, lp)
        if fname == 'cshift':
            return self._emit_cshift(nm, s, fargs, pad, lp)
        if fname in ('sum', 'product', 'maxval', 'minval') and len(fargs) == 2:
            return self._emit_dim_reduce(fname, nm, s, fargs, pad, lp)
        size = self._array_total_size(s)
        idx = self._new_tmp('i')
        self.pre.append(f'fint {idx};')
        rel = self._array_elem_expr(rhs_node, idx)
        return [pad + lp + f'for ({idx} = 0; {idx} < {size}; {idx}++) '
                f'{cname(nm)}[{idx}] = {rel};']

    def _emit_matmul(self, cnm, sc, fargs, pad, lp):
        # c = matmul(a, b):  a(M,K) * b(K,N) -> c(M,N)
        an, bn = str(fargs[0]), str(fargs[1])
        sa, sb = self.s.get(an), self.s.get(bn)
        M, K = self._extent(sa.dims[0]), self._extent(sa.dims[1])
        N = self._extent(sb.dims[1])
        i, j, l = self._new_tmp('i'), self._new_tmp('j'), self._new_tmp('l')
        acc = self._new_tmp('acc')
        for v in (i, j, l):
            self.pre.append(f'fint {v};')
        self.pre.append(f'{sc.ctype} {acc};')
        cij = self._index_lvalue(cnm, sc, [i, j])
        ail = self._index_lvalue(an, sa, [i, l])
        blj = self._index_lvalue(bn, sb, [l, j])
        return [pad + lp + f'for ({j} = 1; {j} <= {N}; {j}++) '
                f'for ({i} = 1; {i} <= {M}; {i}++) {{',
                pad + f'    {acc} = 0;',
                pad + f'    for ({l} = 1; {l} <= {K}; {l}++) '
                f'{acc} += {ail} * {blj};',
                pad + f'    {cij} = {acc};',
                pad + '}']

    def _emit_reshape(self, bnm, sb, src_node, pad, lp):
        # b = reshape(source, shape): column-major flat copy into b
        an = str(src_node)
        size = self._array_total_size(sb)
        idx = self._new_tmp('i')
        self.pre.append(f'fint {idx};')
        return [pad + lp + f'for ({idx} = 0; {idx} < {size}; {idx}++) '
                f'{cname(bnm)}[{idx}] = {cname(an)}[{idx}];']

    def _emit_cshift(self, bnm, sb, fargs, pad, lp):
        # b = cshift(a, shift): 1-D circular shift (toward lower index if >0)
        an = str(fargs[0])
        if len(sb.dims) != 1:
            raise Unsupported('cshift of a multi-dimensional array')
        n = self._array_total_size(sb)
        sh = self.expr(fargs[1])
        idx = self._new_tmp('i')
        self.pre.append(f'fint {idx};')
        return [pad + lp + f'for ({idx} = 0; {idx} < {n}; {idx}++) '
                f'{cname(bnm)}[{idx}] = '
                f'{cname(an)}[(({idx} + ({sh})) % ({n}) + ({n})) % ({n})];']

    def _emit_dim_reduce(self, fname, vnm, sv, fargs, pad, lp):
        # v = sum/product/maxval/minval(a, dim):  reduce a 2-D array along dim
        an = str(fargs[0])
        sa = self.s.get(an)
        if len(sa.dims) != 2:
            raise Unsupported(f'{fname} with DIM over a non-2-D array')
        dim = const_eval_int(fargs[1], self.s)
        M, N = self._extent(sa.dims[0]), self._extent(sa.dims[1])
        # dim=1 -> result(j) over i (length N); dim=2 -> result(i) over j (M)
        outer, inner = (N, M) if dim == 1 else (M, N)
        o, k = self._new_tmp('i'), self._new_tmp('k')
        acc = self._new_tmp('acc')
        for v in (o, k):
            self.pre.append(f'fint {v};')
        self.pre.append(f'{sv.ctype} {acc};')
        elem = (self._index_lvalue(an, sa, [k, o]) if dim == 1
                else self._index_lvalue(an, sa, [o, k]))
        vo = self._index_lvalue(vnm, sv, [o])
        if fname in ('sum', 'product'):
            op = '+=' if fname == 'sum' else '*='
            body = [pad + f'    {acc} = {"0" if fname == "sum" else "1"};',
                    pad + f'    for ({k} = 1; {k} <= {inner}; {k}++) '
                    f'{acc} {op} {elem};',
                    pad + f'    {vo} = {acc};']
        else:
            cmp = '>' if fname == 'maxval' else '<'
            first = (self._index_lvalue(an, sa, ['1', o]) if dim == 1
                     else self._index_lvalue(an, sa, [o, '1']))
            body = [pad + f'    {acc} = {first};',
                    pad + f'    for ({k} = 2; {k} <= {inner}; {k}++) '
                    f'if ({elem} {cmp} {acc}) {acc} = {elem};',
                    pad + f'    {vo} = {acc};']
        return ([pad + lp + f'for ({o} = 1; {o} <= {outer}; {o}++) {{']
                + body + [pad + '}'])

    def _callish(self, node):
        """(lower-name, [arg nodes]) for a call-shaped node, else (None, None)."""
        k = cls(node)
        if k in ('Intrinsic_Function_Reference', 'Function_Reference',
                 'Part_Ref', 'Structure_Constructor'):
            argspec = node.children[1]
            args = (list(argspec.children) if argspec is not None
                    and cls(argspec).endswith('_List') else
                    [argspec] if argspec is not None else [])
            return str(node.children[0]).lower(), args
        return None, None

    def _emit_transpose(self, bnm, sb, src_node, pad, lp):
        # b = transpose(a):  b(i,j) = a(j,i),  b is (Nb, Mb)
        anm = str(src_node)
        sa = self.s.get(anm)
        nb, mb = self._extent(sb.dims[0]), self._extent(sb.dims[1])
        iv, jv = self._new_tmp('i'), self._new_tmp('j')
        self.pre.append(f'fint {iv};')
        self.pre.append(f'fint {jv};')
        blv = self._index_lvalue(bnm, sb, [iv, jv])
        arv = self._index_lvalue(anm, sa, [jv, iv])
        return [pad + lp + f'for ({jv} = 1; {jv} <= {mb}; {jv}++) '
                f'for ({iv} = 1; {iv} <= {nb}; {iv}++) {blv} = {arv};']

    # -- WHERE (masked array assignment) ------------------------------------

    def _elem_assign(self, assign, idx):
        """One whole-array assignment `lhs = rhs` at flat element `idx`."""
        lhs = assign.children[0]
        nm = (str(lhs.children[0]) if cls(lhs) == 'Part_Ref' else str(lhs))
        return f'{cname(nm)}[{idx}] = {self._array_elem_expr(assign.children[2], idx)};'

    def _where_size(self, assigns):
        for a in assigns:
            lhs = a.children[0]
            nm = (str(lhs.children[0]) if cls(lhs) == 'Part_Ref' else str(lhs))
            s = self.s.get(nm)
            if s is not None and s.is_array:
                return self._array_total_size(s)
        raise Unsupported('WHERE with no array assignment')

    def where_stmt(self, node, indent):
        pad = '    ' * indent
        lp = self._label_prefix(node)
        mask, assign = node.children[0], node.children[1]
        idx = self._new_tmp('i')
        self.pre.append(f'fint {idx};')
        size = self._where_size([assign])
        return [pad + lp + f'for ({idx} = 0; {idx} < {size}; {idx}++) '
                f'if ({self._array_elem_expr(mask, idx)}) {self._elem_assign(assign, idx)}']

    def where_construct(self, node, indent):
        pad = '    ' * indent
        kids = node.children
        mask = kids[0].children[0]              # Where_Construct_Stmt(mask)
        where_body, else_body, in_else = [], [], False
        for st in kids[1:]:
            kc = cls(st)
            if kc == 'Elsewhere_Stmt':
                if st.children[1] is not None:
                    raise Unsupported('masked ELSEWHERE')
                in_else = True
            elif kc == 'End_Where_Stmt':
                break
            elif kc == 'Assignment_Stmt':
                (else_body if in_else else where_body).append(st)
            else:
                raise Unsupported(f'WHERE body statement {kc}')
        idx = self._new_tmp('i')
        self.pre.append(f'fint {idx};')
        size = self._where_size(where_body + else_body)
        ipad = pad + '    '
        out = [pad + f'for ({idx} = 0; {idx} < {size}; {idx}++) {{',
               ipad + f'if ({self._array_elem_expr(mask, idx)}) {{']
        out += [ipad + '    ' + self._elem_assign(a, idx) for a in where_body]
        if else_body:
            out.append(ipad + '} else {')
            out += [ipad + '    ' + self._elem_assign(a, idx) for a in else_body]
        out.append(ipad + '}')
        out.append(pad + '}')
        return out

    # -- ASSOCIATE ----------------------------------------------------------

    def associate_construct(self, node, indent):
        pad = '    ' * indent
        kids = node.children
        out = [pad + '{']
        ipad = pad + '    '
        bindings, btypes = {}, {}
        for a in walk(kids[0], f03.Association):
            name = str(a.children[0]).lower()
            sel = a.children[2]
            ssym = self.s.get(str(sel.children[0])) if cls(sel) == 'Part_Ref' \
                else (self.s.get(str(sel)) if cls(sel) == 'Name' else None)
            if cls(sel) == 'Name' and not (ssym and ssym.is_array):
                bindings[name] = f'({self.lhs(sel)})'   # scalar alias
                btypes[name] = self.expr_ctype(sel)
            elif (cls(sel) == 'Part_Ref' and ssym and ssym.is_array
                  and not any(cls(x) == 'Subscript_Triplet'
                              for x in sel.children[1].children)):
                bindings[name] = f'({self.part_ref(sel)})'   # element alias
                btypes[name] = ssym.ctype
            elif cls(sel) in ('Name', 'Part_Ref') and ssym and ssym.is_array:
                raise Unsupported('ASSOCIATE with an array selector')
            else:                                  # expression: evaluate once
                ct = self.expr_ctype(sel)
                tmp = self._new_tmp('as')
                out.append(ipad + f'{ct} {tmp} = {self.expr(sel)};')
                bindings[name], btypes[name] = tmp, ct
        saved, savedt = dict(self._assoc), dict(self._assoc_ctype)
        self._assoc.update(bindings)
        self._assoc_ctype.update(btypes)
        try:
            for st in kids[1:-1]:
                self.stmt(st, out, indent + 1)
        finally:
            self._assoc, self._assoc_ctype = saved, savedt
        out.append(pad + '}')
        return out

    # -- FORALL -------------------------------------------------------------

    def forall(self, node, assigns, indent):
        pad = '    ' * indent
        header = walk(node, f03.Forall_Header)[0]
        if header.children[1] is not None:
            raise Unsupported('masked FORALL')
        specs = walk(header, f03.Forall_Triplet_Spec)
        if len(specs) != 1:
            raise Unsupported('multi-index FORALL')
        sp = specs[0]
        var = cname(str(sp.children[0]))
        lo, hi = self.expr(sp.children[1]), self.expr(sp.children[2])
        step = self.expr(sp.children[3]) if sp.children[3] is not None else '1'
        inc = f'{var}++' if step == '1' else f'{var} += {step}'
        # one loop per assignment: each is completed over the whole index set
        # before the next (FORALL statement-ordering semantics)
        out = []
        for assign in assigns:
            out.append(pad + f'for ({var} = {lo}; {var} <= {hi}; {inc}) {{')
            self.stmt(assign, out, indent + 1)
            out.append(pad + '}')
        return out

    # -- formatted output ---------------------------------------------------

    def _output_items(self, node):
        if node is None:
            return []
        if cls(node).endswith('_List'):
            return list(node.children)
        return [node]

    def _flatten_output(self, items):
        """Expand whole-array and implied-do output items into individual
        (c_expr, ctype) element entries; other items pass through as AST nodes.
        Only statically-sized expansions are supported (loud otherwise)."""
        out = []
        for it in items:
            if self._is_whole_array(it):
                nm = (str(it.children[0]) if cls(it) == 'Part_Ref'
                      else str(it))
                s = self.s.get(nm)
                try:
                    size = int(self._array_total_size(s))
                except ValueError:
                    raise Unsupported('whole-array output of unknown size')
                out.extend((f'{cname(nm)}[{j}]', s.ctype) for j in range(size))
            elif cls(it) == 'Io_Implied_Do':
                out.extend(self._expand_implied_do(it))
            else:
                out.append(it)
        return out

    def _expand_implied_do(self, node):
        objs = self._output_items(node.children[0])
        cc = node.children[1].children          # (var, start, end, [step])
        var = str(cc[0]).lower()
        lo = const_eval_int(cc[1], self.s)
        hi = const_eval_int(cc[2], self.s)
        step = const_eval_int(cc[3], self.s) if len(cc) > 3 and cc[3] else 1
        out = []
        for v in range(lo, hi + (1 if step > 0 else -1), step):
            for obj in objs:
                out.append(self._index_obj_at(obj, var, v))
        return out

    def _index_obj_at(self, obj, var, v):
        """The (c_expr, ctype) for an implied-do object at index value v. The
        loop index itself and arrayname(loop_index) over a 1-D array are
        supported."""
        if cls(obj) == 'Name' and str(obj).lower() == var:
            return str(v), 'fint'
        if cls(obj) != 'Part_Ref':
            raise Unsupported(f'implied-do object {obj}')
        nm = str(obj.children[0])
        s = self.s.get(nm)
        subs = list(obj.children[1].children)
        if (s is None or not s.is_array or len(subs) != 1
                or cls(subs[0]) != 'Name' or str(subs[0]).lower() != var):
            raise Unsupported('implied-do object must be array(index)')
        lo = const_eval_int(s.dims[0][0], self.s) if s.dims[0][0] else 1
        return f'{cname(nm)}[{v - lo}]', s.ctype

    def _emit_data(self, cfmt, cargs, conv, kind, cexpr, ctype):
        if kind == 'i':
            if ctype == 'flong':
                cfmt.append(conv[:-1] + 'lld')      # %...d -> %...lld
                cargs.append(f'(long long)({cexpr})')
            else:
                cfmt.append(conv)
                cargs.append(f'(int)({cexpr})')
        else:
            cfmt.append(conv)
            cargs.append(f'(double)({cexpr})')

    def _impl_const(self, impl):
        cc = impl.children[1].children          # (var, start, end, [step])
        try:
            const_eval_int(cc[1], self.s)
            const_eval_int(cc[2], self.s)
            if len(cc) > 3 and cc[3] is not None:
                const_eval_int(cc[3], self.s)
            return True
        except Unsupported:
            return False

    def _implied_do_loop(self, fmt_node, atoms, impl):
        """A runtime print loop for  (objs..., var=lo,hi[,step])  with a
        non-constant bound. Requires one record per iteration (the number of
        data descriptors equals the number of objects)."""
        objs = self._output_items(impl.children[0])
        if sum(1 for a in atoms if a[0] == 'data') != len(objs):
            raise Unsupported('runtime implied-do: format/object count mismatch')
        cc = impl.children[1].children
        var = cname(str(cc[0]))
        lo, hi = self.expr(cc[1]), self.expr(cc[2])
        step = self.expr(cc[3]) if len(cc) > 3 and cc[3] is not None else '1'
        body = self._emit_printf(fmt_node, impl.children[0])
        if step == '1':
            head = f'for ({var} = {lo}; {var} <= {hi}; {var}++)'
        elif step.lstrip('-').isdigit():
            cmp = '>=' if step.startswith('-') else '<='
            head = f'for ({var} = {lo}; {var} {cmp} {hi}; {var} += {step})'
        else:
            head = (f'for ({var} = {lo}; {step} >= 0 ? {var} <= {hi} : '
                    f'{var} >= {hi}; {var} += {step})')
        return f'{head} {body}'

    def _emit_printf(self, fmt_node, items_node):
        if cls(fmt_node) != 'Char_Literal_Constant':
            raise Unsupported(f'list-directed / runtime format: {fmt_node}')
        inner = _unquote_fortran(str(fmt_node)).strip()
        if inner.startswith('(') and inner.endswith(')'):
            inner = inner[1:-1]
        atoms = parse_format(inner)
        raw = self._output_items(items_node)
        # an implied-do with a runtime bound becomes a print loop
        if len(raw) == 1 and cls(raw[0]) == 'Io_Implied_Do' \
                and not self._impl_const(raw[0]):
            return self._implied_do_loop(fmt_node, atoms, raw[0])
        items = self._flatten_output(raw)

        cfmt, cargs, ii, n = [], [], 0, len(items)
        if not items:
            for a in atoms:
                if a[0] == 'lit':
                    cfmt.append(_c_escape(a[1]))
                elif a[0] == 'nl':
                    cfmt.append('\\n')
                else:               # a data descriptor with no item: stop
                    break
        else:
            if not any(a[0] == 'data' for a in atoms):
                raise Unsupported('output items but no data edit descriptor')
            while ii < n:
                stopped = False
                for a in atoms:
                    if a[0] == 'lit':
                        cfmt.append(_c_escape(a[1]))
                    elif a[0] == 'nl':
                        cfmt.append('\\n')
                    else:
                        if ii >= n:
                            stopped = True
                            break
                        item, kind = items[ii], a[2]
                        ii += 1
                        if kind == 'a':
                            aw = a[1]           # field width, or None for bare A
                            if not isinstance(item, tuple) and \
                                    cls(item) == 'Char_Literal_Constant':
                                text = _unquote_fortran(str(item))
                                if aw is not None:
                                    text = (' ' * (aw - len(text)) + text
                                            if aw >= len(text) else text[:aw])
                                cfmt.append(_c_escape(text))
                                continue
                            ptr, ln = self._char_operand(item)
                            if ln.isdigit():
                                L = int(ln)
                                if aw is None:
                                    cfmt.append(f'%.{L}s')
                                else:
                                    cfmt.append(f'%{aw}.{min(aw, L)}s')
                                cargs.append(ptr)
                            elif aw is None:
                                cfmt.append('%.*s')
                                cargs.append(f'(int)({ln})')
                                cargs.append(ptr)
                            else:
                                raise Unsupported('Aw of a runtime-length string')
                            continue
                        if isinstance(item, tuple):
                            cexpr, ct = item
                        else:
                            cexpr, ct = self.expr(item), self.expr_ctype(item)
                        self._emit_data(cfmt, cargs, a[1], kind, cexpr, ct)
                if stopped:
                    break
                if ii < n:                  # format reversion -> new record
                    cfmt.append('\\n')
        cfmt.append('\\n')
        fmtstr = ''.join(cfmt)
        if cargs:
            return f'printf("{fmtstr}", {", ".join(cargs)});'
        return f'printf("{fmtstr}");'

    def print_stmt(self, node):
        return self._emit_printf(node.children[0], node.children[1]
                                 if len(node.children) > 1 else None)

    def write_stmt(self, node):
        ctrl = node.children[0]
        specs = list(ctrl.children) if cls(ctrl).endswith('_List') else [ctrl]
        unit, fmt = None, None
        for sp in specs:
            val = sp.children[1]
            if cls(val) in ('Io_Unit', 'Int_Literal_Constant') and unit is None:
                unit = str(val)
            elif cls(val) == 'Char_Literal_Constant' and fmt is None:
                fmt = val
        if unit not in ('*', '6'):          # only stdout is handled
            raise Unsupported(f'WRITE to unit {unit}')
        items = node.children[1] if len(node.children) > 1 else None
        return self._emit_printf(fmt, items)

    def _mvbits(self, args):
        # MVBITS(from, frompos, len, to, topos): copy a bit field into `to`
        fr, fp, ln = self.expr(args[0]), self.expr(args[1]), self.expr(args[2])
        tp = self.expr(args[4])
        to_lv, to_rv = self.lhs(args[3]), self.expr(args[3])
        one = '(flong)1' if self.expr_ctype(args[3]) == 'flong' else '1'
        m = f'(({one} << ({ln})) - 1)'
        field = f'((({fr}) >> ({fp})) & {m})'
        return (f'{to_lv} = (({to_rv}) & ~({m} << ({tp}))) | '
                f'(({field}) << ({tp}));')

    def call_stmt(self, node):
        nm = str(node.children[0])
        argspec = node.children[1]
        args = list(argspec.children) if argspec is not None else []
        return f'{self.call_name(nm)}({self._call_args(nm, args)});'

    def _call_args(self, nm, args):
        # For a same-file callee we know its parameter types, so cast each
        # actual argument to match. Fortran passes arguments untyped, so a
        # real array routinely lands on a complex*16 parameter (and vice
        # versa); the cast reproduces that reinterpretation and keeps the C
        # type-checker quiet. Cross-file callees are declared with unspecified
        # args, so they need no cast. Character actuals pass a pointer plus a
        # trailing hidden length (gfortran ABI).
        nml = nm.lower()
        callee = (getattr(self.s, 'registry', {}).get(nml)
                  if nml in self.s.file_procs else None)
        cargs, lens = [], []
        for i, a in enumerate(args):
            if self.expr_ctype(a) == 'char':
                ptr, ln = self._char_operand(a)
                cargs.append(ptr)
                lens.append(ln)
                continue
            carg = self.actual_arg(a)
            if callee is not None and i < len(callee.args):
                pt = callee.get(callee.args[i]).ctype
                if pt != 'char':
                    carg = f'({pt} *){carg}'
            cargs.append(carg)
        return ', '.join(cargs + lens)

    def alloc_stmt(self, node):
        # ALLOCATE(a(dims), b(dims), ..., stat=ierr) -> one malloc per target
        out = []
        for alc in walk(node, f03.Allocation):
            nm = str(alc.children[0])
            specs = walk(alc.children[1], f03.Allocate_Shape_Spec)
            sym = self.s.get(nm)
            # record runtime bounds so later indexing knows leading dims
            sym.dims = [tuple(sp.children) for sp in specs]
            # Fortran fixes the array's shape at ALLOCATE; capture each
            # non-constant extent (and lower bound) into a temp so later
            # reassignment of a bound variable does not change indexing. The
            # temp is declared at function scope by _register_alloc_captures.
            exts, dim_c = [], []
            for k, sp in enumerate(specs):
                lo, hi = sp.children
                lo_c = self._lo(lo) if lo is not None else '1'
                ext_c = self._extent_from_alloc(sp)
                if _alloc_dim_needs_capture(sp, self.s):
                    cap = f'{cname(nm)}_acap{k}'
                    out.append(f'{cap} = {ext_c};')
                    ext_c = cap
                exts.append(ext_c)
                dim_c.append((lo_c, ext_c))
            sym.dim_c = dim_c
            size = ' * '.join(f'({e})' for e in exts)
            ctype = sym.ctype
            out.append(f'{cname(nm)} = ({ctype} *)malloc(({size}) * sizeof({ctype}));')
        # stat=ierr : we assume malloc succeeds, so report success (0)
        for opt in walk(node, f03.Alloc_Opt):
            if str(opt.children[0]).upper() == 'STAT':
                out.append(f'{self.lhs(opt.children[1])} = 0;')
        return out

    def _extent_from_alloc(self, sp):
        lo, hi = sp.children
        hs = self.expr(hi)
        if lo is None:
            return hs
        return f'({hs}) - ({self.expr(lo)}) + 1'

    def if_construct(self, node, indent):
        pad = '    ' * indent
        out = []
        kids = node.children
        # If_Then_Stmt(cond), stmts..., [Else_Stmt, stmts...], End_If_Stmt
        i = 0
        cond = self.expr(walk(kids[0], )[0]) if False else None
        # first child is If_Then_Stmt
        cond_node = kids[0].children[0]
        out.append(pad + f'if ({self.expr(cond_node)}) {{')
        i = 1
        while i < len(kids) and cls(kids[i]) not in ('Else_Stmt', 'Else_If_Stmt', 'End_If_Stmt'):
            self.stmt(kids[i], out, indent + 1)
            i += 1
        while i < len(kids) and cls(kids[i]) == 'Else_If_Stmt':
            c = self.expr(kids[i].children[0])
            out.append(pad + f'}} else if ({c}) {{')
            i += 1
            while i < len(kids) and cls(kids[i]) not in ('Else_Stmt', 'Else_If_Stmt', 'End_If_Stmt'):
                self.stmt(kids[i], out, indent + 1)
                i += 1
        if i < len(kids) and cls(kids[i]) == 'Else_Stmt':
            out.append(pad + '} else {')
            i += 1
            while i < len(kids) and cls(kids[i]) != 'End_If_Stmt':
                self.stmt(kids[i], out, indent + 1)
                i += 1
        out.append(pad + '}')
        return out

    def _case_cond(self, sel, crange):
        """A C condition for one CASE selector against the selector temp `sel`:
        single values, value lists, and (open) ranges joined by ||."""
        items = (list(crange.children) if cls(crange).endswith('_List')
                 else [crange])
        parts = []
        for it in items:
            if cls(it) == 'Case_Value_Range':
                lo, hi = it.children
                if lo is not None and hi is not None:
                    parts.append(f'({sel} >= {self.expr(lo)} && '
                                 f'{sel} <= {self.expr(hi)})')
                elif lo is not None:
                    parts.append(f'({sel} >= {self.expr(lo)})')
                else:
                    parts.append(f'({sel} <= {self.expr(hi)})')
            else:
                parts.append(f'{sel} == {self.expr(it)}')
        return ' || '.join(parts)

    def case_construct(self, node, indent):
        pad = '    ' * indent
        kids = node.children
        lp = self._label_prefix(kids[0])
        sel_node = kids[0].children[0]          # Select_Case_Stmt(selector)
        seltype = self.expr_ctype(sel_node)
        sel = self._new_tmp('sel')
        out = [pad + lp + f'{seltype} {sel} = {self.expr(sel_node)};']
        # gather (value-range-or-None, body) clauses
        clauses, i = [], 1
        while i < len(kids) and cls(kids[i]) != 'End_Select_Stmt':
            if cls(kids[i]) == 'Case_Stmt':
                crange = kids[i].children[0].children[0]   # Case_Selector inner
                i += 1
                body = []
                while i < len(kids) and cls(kids[i]) not in (
                        'Case_Stmt', 'End_Select_Stmt'):
                    body.append(kids[i]); i += 1
                clauses.append((crange, body))
            else:
                i += 1
        normal = [(c, b) for c, b in clauses if c is not None]
        default = [b for c, b in clauses if c is None]
        started = False
        for crange, body in normal:
            head = (f'}} else if ({self._case_cond(sel, crange)}) {{' if started
                    else f'if ({self._case_cond(sel, crange)}) {{')
            out.append(pad + head)
            for b in body:
                self.stmt(b, out, indent + 1)
            started = True
        if default:
            out.append(pad + ('} else {' if started else 'if (1) {'))
            for b in default[0]:
                self.stmt(b, out, indent + 1)
            started = True
        if started:
            out.append(pad + '}')
        return out

    def _loop_snapshot(self, node, vtype, out, pad):
        """A loop bound/step: inlined if a literal constant, otherwise captured
        into a temp (declared into `out`) so it is evaluated once at entry."""
        if cls(node) in ('Int_Literal_Constant', 'Signed_Int_Literal_Constant'):
            return self.expr(node)
        tmp = self._new_tmp('bnd')
        out.append(pad + f'{vtype} {tmp} = {self.expr(node)};')
        return tmp

    def do_construct(self, node, indent):
        pad = '    ' * indent
        kids = node.children
        do_stmt = kids[0]
        loop_ctrl = None
        for c in walk(do_stmt, f03.Loop_Control):
            loop_ctrl = c
        body = [c for c in kids[1:] if cls(c) != 'End_Do_Stmt']
        out = []
        lp = self._label_prefix(do_stmt)
        if loop_ctrl is None:
            out.append(pad + lp + 'for (;;) {')
        else:
            lc = loop_ctrl.children
            # DO WHILE: while clause stored as (None, None, while_expr?) -> check
            if lc[0] is not None and cls(lc[0]) != 'Name' and not isinstance(lc[0], tuple):
                # while form: Loop_Control(scalar_logical_expr, None, ...)
                pass
            counter = loop_ctrl.children[1]  # (Name, [start, end, step])
            while_expr = loop_ctrl.children[0]
            if counter is not None:
                # The loop variable may be a scalar dummy (passed by reference,
                # so a pointer in C) -- e.g. an INFO output used as a counter in
                # a singularity check. Use its lvalue form and parenthesize so
                # the C increment is `(*info)++`, not the mis-parsed `*info++`.
                var = self.lhs(counter[0])
                if var.startswith('*'):
                    var = f'({var})'
                vsym = self.s.get(str(counter[0]))
                vtype = vsym.ctype if vsym and vsym.ctype else 'fint'
                bounds = counter[1]
                start = self.expr(bounds[0])
                step_node = bounds[2] if len(bounds) > 2 else None
                # Fortran fixes the trip count when the loop is entered. Snapshot
                # the end bound and (variable) step into temps so modifying them
                # in the body cannot change the iteration count.
                snaps = []
                end = self._loop_snapshot(bounds[1], vtype, snaps, pad)
                if step_node is None:
                    step = '1'
                else:
                    step = self._loop_snapshot(step_node, vtype, snaps, pad)
                # A snapshot is an initialized declaration emitted just above the
                # `for`. If the loop carries a Fortran statement label, a `goto`
                # targeting it must not jump over those initializers (skipping an
                # initialized declaration leaves the temp indeterminate -> a
                # garbage trip count). Anchor the label to a null statement above
                # the snapshots so the jump lands before them.
                forlp = lp
                if lp and snaps:
                    out.append(pad + lp + ';')
                    forlp = ''
                out.extend(snaps)
                if step == '1':
                    out.append(pad + forlp + f'for ({var} = {start}; {var} <= {end}; {var}++) {{')
                elif step.lstrip('-').isdigit():
                    cmp = '>=' if step.startswith('-') else '<='
                    out.append(pad + forlp + f'for ({var} = {start}; {var} {cmp} {end}; {var} += {step}) {{')
                else:
                    # snapshotted variable step: choose the direction at runtime
                    out.append(pad + forlp + f'for ({var} = {start}; '
                               f'{step} >= 0 ? {var} <= {end} : {var} >= {end}; '
                               f'{var} += {step}) {{')
            elif while_expr is not None:
                out.append(pad + lp + f'while ({self.expr(while_expr)}) {{')
            else:
                out.append(pad + lp + 'for (;;) {')
        for b in body:
            self.stmt(b, out, indent + 1)
        out.append(pad + '}')
        return out


# --------------------------------------------------------------------------
# Const evaluation (for local array sizes)
# --------------------------------------------------------------------------

def const_eval_int(node, scope, binds=None):
    k = cls(node)
    if k == 'Int_Literal_Constant':
        return int(str(node.children[0]))
    if k == 'Name':
        if binds and str(node).lower() in binds:    # bound loop index
            return binds[str(node).lower()]
        s = scope.get(str(node))
        if s and s.is_param and s.value is not None:
            return const_eval_int(s.value, scope, binds)
    if k == 'Parenthesis':
        return const_eval_int(node.children[1], scope, binds)
    items = node.children
    if len(items) == 3 and isinstance(items[1], str):
        a = const_eval_int(items[0], scope, binds)
        b = const_eval_int(items[2], scope, binds)
        return {'+': a + b, '-': a - b, '*': a * b, '/': a // b}[items[1]]
    raise Unsupported(f'const-eval {k}')


# --------------------------------------------------------------------------
# Top-level generation
# --------------------------------------------------------------------------

def parse_file(path):
    # fparser2 builds its parse tree by deep recursion. Large machine-generated
    # sources -- tens of thousands of statements, e.g. fmm3d's Helmholtz
    # quadrature tables (hnumphys/hnumfour) and the INCLUDEd weight files pulled
    # into hwts3e -- recurse far past CPython's default limit (1000) and default
    # thread stack, which otherwise shows up as a RecursionError or a crash.
    # Raise the recursion limit and run the parse on a worker thread with a
    # large stack so these still transpile.
    def _parse():
        reader = FortranFileReader(path, ignore_comments=True)
        parser = ParserFactory().create(std="f2008")
        return parser(reader)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))
    result = {}

    def _run():
        try:
            result['tree'] = _parse()
        except BaseException as exc:        # re-raised on the calling thread
            result['err'] = exc

    try:
        old_size = threading.stack_size(1 << 29)   # 512 MiB worker stack
    except (ValueError, RuntimeError):
        old_size = None
    try:
        t = threading.Thread(target=_run)
        t.start()
        t.join()
    finally:
        if old_size is not None:
            threading.stack_size(old_size)
    if 'err' in result:
        raise result['err']
    return result['tree']


def collect_procs(tree):
    procs = []
    for sp in walk(tree, (f03.Subroutine_Subprogram, f03.Function_Subprogram)):
        procs.append(sp)
    return procs


def emit_signature(scope):
    if scope.is_main:
        return 'int main(void)'
    params = []
    for nm in scope.args:
        s = scope.get(nm)
        const = 'const ' if s.intent == 'in' else ''
        params.append(f'{const}{s.ctype} *{cname(nm)}')
    # hidden trailing length argument per character dummy (gfortran ABI)
    for nm in scope.char_dummies:
        params.append(f'fint {cname(nm)}_len')
    plist = ', '.join(params) if params else 'void'
    if scope.is_function:
        ret = scope.result_ctype
        return f'{ret} FNAME({scope.name})({plist})'
    return f'void FNAME({scope.name})({plist})'


def emit_decls(scope):
    lines = []
    arg_set = set(scope.args)               # this scope's parameters (e.g. an
                                            # ENTRY's args may be locals of the
                                            # parent routine -- emit as params)
    for nm in scope.syms:
        s = scope.get(nm)
        cn = cname(nm)
        if s.is_dummy or nm in scope.stmt_funcs or nm in arg_set:
            continue                        # stmt-function names are not vars
        if scope.is_function and nm == scope.result_name:
            lines.append(f'    {s.ctype} {cn};')
            continue
        if s.is_param:
            from_val = const_eval_int(s.value, scope) if s.ctype == 'fint' else None
            val = from_val if from_val is not None else _scalar_val(scope, s)
            lines.append(f'    const {s.ctype} {cn} = {val};')
            continue
        if s.is_alloc:
            lines.append(f'    {s.ctype} *{cn} = NULL;')
            continue
        # a DATA/initialized or SAVEd local persists across calls -> `static`
        has_init = (s.value is not None or s.data_values is not None
                    or s.data_map is not None)
        stat = 'static ' if (s.is_save or scope.all_saved or has_init) else ''
        if s.ctype == 'char' and not s.is_array:
            lines.append(f'    {stat}char {cn}[{s.char_len}];')
            continue
        if s.is_array:
            init = ''
            if s.data_values is not None:
                em = Emitter(scope)
                init = ' = {' + ', '.join(em.expr(v) for v in s.data_values) + '}'
            elif s.data_map is not None:
                em = Emitter(scope)
                init = ' = {' + ', '.join(
                    f'[{o}] = {em.expr(v)}'
                    for o, v in sorted(s.data_map.items())) + '}'
            try:
                size = 1
                for lo, hi in s.dims:
                    h = const_eval_int(hi, scope)
                    l = const_eval_int(lo, scope) if lo is not None else 1
                    size *= (h - l + 1)
                lines.append(f'    {stat}{s.ctype} {cn}[{size}]{init};')
            except Unsupported:
                # automatic array sized by a dummy/expression -> C99 VLA
                em = Emitter(scope)
                ext = [em._extent(d) for d in s.dims]
                size_expr = ' * '.join(f'({e})' for e in ext)
                lines.append(f'    {s.ctype} {cn}[{size_expr}];')
            continue
        init = ''
        if s.value is not None:
            init = f' = {_scalar_val(scope, s)}'
        lines.append(f'    {stat}{s.ctype} {cn}{init};')
    return lines


def _scalar_val(scope, s):
    em = Emitter(scope)
    return em.expr(s.value)


def _alloc_dim_needs_capture(sp, scope):
    """True if an ALLOCATE shape spec's extent is not a compile-time constant
    (so its value must be captured at allocation rather than re-read later)."""
    lo, hi = sp.children
    try:
        const_eval_int(hi, scope)
        if lo is not None:
            const_eval_int(lo, scope)
        return False
    except Unsupported:
        return True


def _register_alloc_captures(scope):
    """Pre-register a function-scope flong temp for every non-constant ALLOCATE
    extent in the scope, so alloc_stmt can capture the extent there (Sym.dim_c)
    and emit_decls declares the temp. Must run before emit_decls."""
    if scope.exec_part is None:
        return
    for alc in walk(scope.exec_part, f03.Allocation):
        shape = alc.children[1] if len(alc.children) > 1 else None
        if shape is None:
            continue
        nm = str(alc.children[0])
        for k, sp in enumerate(walk(shape, f03.Allocate_Shape_Spec)):
            if _alloc_dim_needs_capture(sp, scope):
                scope.sym(f'{cname(nm)}_acap{k}').ctype = 'flong'


def entry_scopes(scope):
    """Derived scopes for each ENTRY point of `scope`, sharing its symbol table
    but with the entry's own name, argument list, and body slice."""
    out = []
    for e in getattr(scope, 'entries', ()):
        esc = copy.copy(scope)           # shallow: shares syms / exec_part
        esc.name = e['name']
        esc.args = e['args']
        esc.is_function = False          # fmm3d's entries are all subroutines
        esc.is_main = False
        esc.result_name = None
        esc.result_ctype = None
        esc.exec_start = e['start']
        esc.exec_stop = e['stop']
        esc.entries = ()                 # don't recurse
        esc.char_dummies = [nm for nm in esc.args
                            if (esc.get(nm) and esc.get(nm).ctype == 'char'
                                and not esc.get(nm).is_array)]
        for nm in esc.char_dummies:      # assumed-length dummy -> hidden length
            s = esc.get(nm)
            if s.char_len in (None, '*'):
                s.char_len = f'{cname(nm)}_len'
        out.append(esc)
    return out


def emit_proc(scope):
    parts = [_emit_proc_one(scope)]
    for esc in entry_scopes(scope):
        parts.append(_emit_proc_one(esc))
        # fold cross-file calls made from entry bodies into the parent's set so
        # merge_xcalls (which only sees top-level scopes) emits their externs.
        for nm, rt in getattr(esc, '_xcalls', {}).items():
            if rt != 'void' or nm not in scope._xcalls:
                scope._xcalls[nm] = rt
    return '\n\n'.join(parts)


def _emit_proc_one(scope):
    out = []
    _register_alloc_captures(scope)         # before emit_decls (declares temps)
    out.append(emit_signature(scope) + '\n{')
    decls = emit_decls(scope)
    out.extend(decls)
    if decls:
        out.append('')
    em = Emitter(scope)
    body = []
    if scope.exec_part is not None:
        for st in scope.exec_part.children[scope.exec_start:scope.exec_stop]:
            em.stmt(st, body, 1)
    scope._xcalls = em.xcalls
    out.extend(body)
    # If the body already ends in an unconditional `return`, the fall-through
    # epilogue (allocatable frees + a final return) is dead code -- skip it.
    last = next((ln for ln in reversed(body) if ln.strip()), '')
    if not last.strip().startswith('return'):
        # Fortran auto-deallocates local allocatables at routine end; free them
        # on the fall-through path (explicit Return_Stmts free their own;
        # DEALLOCATE nulls the pointer, so free(NULL) here is a safe no-op).
        out.extend('    ' + f for f in em._alloc_frees())
        if scope.is_function:
            out.append(f'    return {cname(scope.result_name)};')
        elif scope.is_main:
            out.append('    return 0;')
    out.append('}')
    return '\n'.join(out)


def build_scopes(path, only):
    tree = parse_file(path)
    procs = collect_procs(tree)
    proc_names = set()
    for p in procs:
        proc_names.add(str(walk(p.children[0], f03.Name)[0]).lower())
    registry = {}
    scopes = []
    for p in procs:
        sc = Scope(p, proc_names)   # all proc names known (same-file calls)
        registry[sc.name] = sc
        if only is None or sc.name in only:
            scopes.append(sc)       # ...but only emit the selected ones
    # the main program becomes int main(); it is never selected by `only`
    if only is None:
        for mp in walk(tree, f03.Main_Program):
            scopes.append(Scope(mp, proc_names))
    for sc in scopes:
        sc.registry = registry      # for same-file call arg typing
    return scopes


_MATH_FUNCS = ('sqrt', 'log', 'exp', 'fabs', 'pow', 'atan', 'atan2', 'sin',
               'cos', 'tan', 'asin', 'acos', 'copysign', 'trunc', 'round',
               'fmod', 'floor', 'ceil', 'sinh', 'cosh', 'tanh', 'log10')
_CPLX_FUNCS = ('cpow', 'cabs', 'cimag', 'creal', 'conj', 'clog', 'cexp',
               'csqrt', ' * I', '+ 0.0 * I')


_POWI = {
    'fmm_cpowi': '''static fcomplex fmm_cpowi(fcomplex a, fint n) {
    fcomplex pw = 1.0, x = a; fint u = 0;
    if (n != 0) {
        if (n < 0) { u = 1; n = -n; }
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    }
    return u ? 1.0 / pw : pw;
}''',
    'fmm_dpowi': '''static double fmm_dpowi(double a, fint n) {
    double pw = 1.0, x = a; fint u = 0;
    if (n != 0) {
        if (n < 0) { u = 1; n = -n; }
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    }
    return u ? 1.0 / pw : pw;
}''',
    'fmm_fpowi': '''static float fmm_fpowi(float a, fint n) {
    float pw = 1.0f, x = a; fint u = 0;
    if (n != 0) {
        if (n < 0) { u = 1; n = -n; }
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    }
    return u ? 1.0f / pw : pw;
}''',
    'fmm_ipowi': '''static fint fmm_ipowi(fint a, fint n) {
    fint pw = 1, x = a;
    if (n < 0) return a == 1 ? 1 : (a == -1 ? ((-n) & 1 ? -1 : 1) : 0);
    if (n != 0)
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    return pw;
}''',
    'fmm_lpowi': '''static flong fmm_lpowi(flong a, fint n) {
    flong pw = 1, x = a;
    if (n < 0) return a == 1 ? 1 : (a == -1 ? ((-n) & 1 ? -1 : 1) : 0);
    if (n != 0)
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    return pw;
}''',
    'fmm_iabs': '''static fint fmm_iabs(fint x) { return x < 0 ? -x : x; }''',
    'fmm_labs': '''static flong fmm_labs(flong x) { return x < 0 ? -x : x; }''',
    'fmm_strcat': '''static void fmm_strcat(char *d, fint n, fint *p,
                                        const char *s, fint sl) {
    fint i; for (i = 0; i < sl && *p < n; i++) d[(*p)++] = s[i];
}''',
    'fmm_strpad': '''static void fmm_strpad(char *d, fint n, fint p) {
    while (p < n) d[p++] = ' ';
}''',
    'fmm_strcmp': '''static fint fmm_strcmp(const char *a, fint na,
                                       const char *b, fint nb) {
    fint i, n = na > nb ? na : nb;
    for (i = 0; i < n; i++) {
        char ca = i < na ? a[i] : ' ', cb = i < nb ? b[i] : ' ';
        if (ca != cb) return ca < cb ? -1 : 1;
    }
    return 0;
}''',
    'fmm_lentrim': '''static fint fmm_lentrim(const char *s, fint n) {
    while (n > 0 && s[n-1] == ' ') n--;
    return n;
}''',
    'fmm_index': '''static fint fmm_index(const char *s, fint ns,
                                     const char *t, fint nt) {
    fint i, j;
    for (i = 0; i + nt <= ns; i++) {
        for (j = 0; j < nt; j++) if (s[i+j] != t[j]) break;
        if (j == nt) return i + 1;
    }
    return 0;
}''',
    'fmm_adjustl': '''static void fmm_adjustl(char *d, const char *s, fint n) {
    fint i = 0, j = 0;
    while (i < n && s[i] == ' ') i++;
    while (i < n) d[j++] = s[i++];
    while (j < n) d[j++] = ' ';
}''',
    'fmm_adjustr': '''static void fmm_adjustr(char *d, const char *s, fint n) {
    fint i = n, j = n;
    while (i > 0 && s[i-1] == ' ') i--;
    while (i > 0) d[--j] = s[--i];
    while (j > 0) d[--j] = ' ';
}''',
    'fmm_isign': '''static fint fmm_isign(fint a, fint b) {
    fint m = a < 0 ? -a : a; return b < 0 ? -m : m;
}''',
    'fmm_lsign': '''static flong fmm_lsign(flong a, flong b) {
    flong m = a < 0 ? -a : a; return b < 0 ? -m : m;
}''',
    'fmm_ishft': '''static fint fmm_ishft(fint i, fint s) {
    if (s <= -32 || s >= 32) return 0;
    return s >= 0 ? (fint)((uint32_t)i << s) : (fint)((uint32_t)i >> (-s));
}''',
    'fmm_lshft': '''static flong fmm_lshft(flong i, fint s) {
    if (s <= -64 || s >= 64) return 0;
    return s >= 0 ? (flong)((uint64_t)i << s) : (flong)((uint64_t)i >> (-s));
}''',
    'fmm_ishftc': '''static fint fmm_ishftc(fint v, fint shift, fint size) {
    uint32_t mask = size >= 32 ? 0xFFFFFFFFu : (((uint32_t)1 << size) - 1);
    uint32_t low = (uint32_t)v & mask;
    fint sh = shift % size; if (sh < 0) sh += size;
    uint32_t rot = sh == 0 ? low : (((low << sh) | (low >> (size - sh))) & mask);
    return (fint)(((uint32_t)v & ~mask) | rot);
}''',
    'fmm_lshftc': '''static flong fmm_lshftc(flong v, fint shift, fint size) {
    uint64_t mask = size >= 64 ? ~(uint64_t)0 : (((uint64_t)1 << size) - 1);
    uint64_t low = (uint64_t)v & mask;
    fint sh = shift % size; if (sh < 0) sh += size;
    uint64_t rot = sh == 0 ? low : (((low << sh) | (low >> (size - sh))) & mask);
    return (flong)(((uint64_t)v & ~mask) | rot);
}''',
}


def _powi_helpers(blob):
    """Static definitions for any integer-power helper the body references."""
    out = []
    for name, defn in _POWI.items():
        if name + '(' in blob:
            out.append(defn)
            out.append('')
    return out


def merge_xcalls(scopes):
    """Cross-file callees collected during emission: name -> C return type
    (a real return type from a function-as-value beats a 'void' from a CALL)."""
    merged = {}
    for sc in scopes:
        for nm, rt in getattr(sc, '_xcalls', {}).items():
            if rt != 'void' or nm not in merged:
                merged[nm] = rt
    return merged


def _default_basename(path):
    return os.path.splitext(os.path.basename(path))[0]


# The generated C targets a tiny support header that defines the ABI helpers
# FNAME, fint, fcomplex and the column-major index macros FA2/FA3/FA4. The
# fmm2d port calls that header "fmm2d_c.h" with an "FMM2D_" include guard
# prefix; both are configurable for use outside fmm2d.
DEFAULT_RUNTIME_HEADER = 'fmm2d_c.h'
DEFAULT_GUARD_PREFIX = 'FMM2D_'


def generate_c(path, basename=None, only=None,
               runtime_header=DEFAULT_RUNTIME_HEADER):
    basename = basename or _default_basename(path)
    scopes = build_scopes(path, only)
    bodies = [emit_proc(sc) for sc in scopes]
    blob = '\n'.join(bodies)
    xcalls = merge_xcalls(scopes)

    needs_alloc = any(s.is_alloc for sc in scopes for s in sc.syms.values())
    needs_exit = 'exit(' in blob          # STOP -> exit(); declared in stdlib.h
    # match e.g. sqrt( and sqrtf( (single-precision libm variant)
    needs_math = any(re.search(re.escape(f) + r'f?\(', blob) for f in _MATH_FUNCS)
    needs_cplx = ('fcomplex' in blob) or any(t in blob for t in _CPLX_FUNCS)
    needs_stdio = 'printf(' in blob
    needs_float = any(t in blob for t in
                      ('DBL_EPSILON', 'DBL_MAX', 'DBL_MIN',
                       'FLT_EPSILON', 'FLT_MAX', 'FLT_MIN'))

    out = []
    out.append('/*')
    out.append(f' * {basename}.c - C translation of {path}')
    out.append(' *')
    out.append(' * Generated by fort2c (fparser2 front end). Do not edit by hand.')
    out.append(' */')
    out.append('')
    if needs_stdio:
        out.append('#include <stdio.h>')
    if needs_alloc or needs_exit:
        out.append('#include <stdlib.h>')
    if needs_math:
        out.append('#include <math.h>')
    if needs_float:
        out.append('#include <float.h>')
    if needs_cplx:
        out.append('#include <complex.h>')
    out.append(f'#include "{basename}.h"')
    out.append('')
    if xcalls:
        out.append('/* cross-file routines (resolved against the Fortran library'
                   ' or other C drop-ins) */')
        for cn in sorted(xcalls):
            out.append(f'extern {xcalls[cn]} {cn}_();')
        out.append('')
    out.extend(_powi_helpers(blob))
    for b in bodies:
        out.append(b)
        out.append('')
    return '\n'.join(out)


def generate_h(path, basename=None, only=None,
               runtime_header=DEFAULT_RUNTIME_HEADER,
               guard_prefix=DEFAULT_GUARD_PREFIX):
    basename = basename or _default_basename(path)
    scopes = build_scopes(path, only)
    guard = f'{guard_prefix}{basename.upper()}_H'
    out = []
    out.append(f'/* {basename}.h - generated by fort2c */')
    out.append(f'#ifndef {guard}')
    out.append(f'#define {guard}')
    out.append('')
    out.append(f'#include "{runtime_header}"')
    out.append('')
    for sc in scopes:
        if sc.is_main:                      # main() is not a public prototype
            continue
        out.append(emit_signature(sc) + ';')
        for esc in entry_scopes(sc):        # ENTRY points are public too
            out.append(emit_signature(esc) + ';')
    out.append('')
    out.append(f'#endif /* {guard} */')
    return '\n'.join(out)
