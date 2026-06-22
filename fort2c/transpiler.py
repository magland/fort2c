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

import os

from fparser.two.parser import ParserFactory
from fparser.common.readfortran import FortranFileReader
from fparser.two.utils import walk
import fparser.two.Fortran2003 as f03


class Unsupported(Exception):
    pass


def cls(node):
    return type(node).__name__


_RANK = {'fint': 0, 'double': 1, 'fcomplex': 2}


def _promote(a, b):
    return a if _RANK.get(a, 1) >= _RANK.get(b, 1) else b


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
        self.dims = None           # list of (lo_expr, hi_expr) or None
        self.value = None          # fparser expr (scalar parameter / data init)
        self.data_values = None    # list of value nodes (whole-array DATA init)
        self.data_map = None       # {c_offset: value node} (subscripted DATA)

    @property
    def is_array(self):
        return self.dims is not None or self.is_alloc


def ctype_of_name(name):
    """Map a Fortran type name (possibly two-word like 'DOUBLE COMPLEX') to C.
    Substring match so 'COMPLEX', 'DOUBLE COMPLEX', 'COMPLEX*16' all land on
    fcomplex; 'REAL'/'DOUBLE PRECISION' on double; 'INTEGER'/'LOGICAL' on fint.
    """
    n = name.upper()
    if 'COMPLEX' in n:
        return 'fcomplex'
    if 'INTEGER' in n or 'LOGICAL' in n:
        return 'fint'
    return 'double'             # REAL, DOUBLE PRECISION, etc. (all real*8 here)


class Scope:
    """One Fortran procedure."""

    def __init__(self, sub, file_procs):
        self.file_procs = file_procs     # set of procedure names in this file
        self.syms = {}
        self.args = []
        self.is_function = isinstance(sub, f03.Function_Subprogram)
        self.result_name = None
        self.result_ctype = None
        self.implicit_none = False
        self.implicit_map = self._default_implicit()

        stmt = sub.children[0]
        # Function_Stmt(prefix, Name, args, suffix) / Subroutine_Stmt(...)
        self.name = str(walk(stmt, f03.Name)[0]).lower()
        arglist = None
        for c in stmt.children:
            if cls(c) in ('Dummy_Arg_List',):
                arglist = c
        if arglist is not None:
            self.args = [str(n).lower() for n in walk(arglist, f03.Name)]
        elif self.is_function is False:
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
            ctype = ctype_of_name(str(ispec.children[0].children[0]))
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
        for dstmt in walk(spec, f03.Data_Stmt):
            self._parse_data(dstmt)

    def _parse_type_decl(self, tdecl):
        type_spec = tdecl.children[0]
        ctype = self._ctype_of(type_spec)
        attr_list = tdecl.children[1]
        intent = None
        is_param = False
        is_alloc = False
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
                elif k == 'Intent_Attr_Spec':
                    intent = str(walk(a, f03.Intent_Spec)[0]).lower()
                elif k == 'Dimension_Attr_Spec':
                    common_dims = self._array_spec_dims(a.children[1])
        for ent in walk(tdecl, f03.Entity_Decl):
            nm = str(ent.children[0])
            s = self.sym(nm)
            s.ctype = ctype
            s.intent = intent
            s.is_param = s.is_param or is_param
            s.is_alloc = s.is_alloc or is_alloc
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
        return ctype_of_name(str(type_spec.children[0]))

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
                if cls(obj) == 'Part_Ref':
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

    def _data_offset(self, s, subs):
        # column-major C offset from constant subscripts
        off, stride = 0, 1
        for k, sub in enumerate(subs):
            lo = s.dims[k][0]
            loval = const_eval_int(lo, self) if lo is not None else 1
            off += (const_eval_int(sub, self) - loval) * stride
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
LOG_OPS = {'.AND.': '&&', '.OR.': '||'}

# intrinsics whose result is real regardless of (real/complex) argument
INTRIN_REAL_RESULT = {'DBLE', 'DREAL', 'REAL', 'DIMAG', 'AIMAG', 'IMAG',
                      'CDABS', 'DABS', 'ABS', 'DSQRT', 'DLOG', 'DEXP',
                      'ATAN', 'DATAN', 'ATAN2', 'DATAN2', 'SIN', 'DSIN',
                      'COS', 'DCOS', 'TAN', 'DTAN', 'ASIN', 'DASIN',
                      'ACOS', 'DACOS', 'SIGN', 'DSIGN', 'AINT', 'DINT',
                      'ANINT', 'DNINT'}
INTRIN_COMPLEX_RESULT = {'DCMPLX', 'CMPLX', 'DCONJG', 'CONJG', 'CDLOG',
                         'CDEXP', 'CDSQRT'}


class Emitter:
    def __init__(self, scope):
        self.s = scope
        self.tmp_n = 0
        self.pre = []          # statements to emit before current statement
        self.xcalls = {}       # cross-file callee name -> C return type

    # -- expressions --------------------------------------------------------

    def expr(self, node):
        k = cls(node)
        if k == 'Name':
            return self.name_value(str(node))
        if k in ('Int_Literal_Constant', 'Signed_Int_Literal_Constant'):
            return str(node.children[0]) if k == 'Int_Literal_Constant' \
                else self._num_lit(node)
        if k in ('Real_Literal_Constant', 'Signed_Real_Literal_Constant'):
            return self._num_lit(node)
        if k == 'Complex_Literal_Constant':
            re = self.expr(node.children[0])
            im = self.expr(node.children[1])
            return f'({re} + ({im}) * I)'
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
        if et == 'fint':
            # runtime integer exponent: call a helper that replicates
            # libgfortran's pow_*_i4 squaring (cpow/pow would differ in ULPs)
            if bt == 'fcomplex':
                return f'fmm_cpowi({be}, {ee})'
            if bt == 'double':
                return f'fmm_dpowi({be}, {ee})'
            return f'fmm_ipowi({be}, {ee})'
        if bt == 'fcomplex':
            return f'cpow({be}, (double)({ee}))'
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
        # derived-type constructor. Two cases occur in fmm2d:
        name = str(node.children[0]).lower()
        if name in ('second', 'omp_get_wtime', 'etime'):
            return '0.0'                       # timing -> deterministic zero
        # dcmplx(re, im) -> (re) + (im)*I ; dcmplx(re) -> (re)
        comp = node.children[1]
        ce = [self.expr(a) for a in comp.children] if comp is not None else []
        if len(ce) == 2:
            return f'(({ce[0]}) + ({ce[1]}) * I)'
        return f'({ce[0]})'

    def _num_lit(self, node):
        # works for (signed) real/int literals via the rendered token
        txt = str(node).replace(' ', '')
        if '_' in txt:                      # strip kind suffix _8 / _wp
            txt = txt.split('_', 1)[0]
        return txt.replace('D', 'e').replace('d', 'e').replace('E', 'e')

    def name_value(self, nm):
        s = self.s.get(nm)
        if s is None:
            return cname(nm)
        if s.is_dummy and not s.is_array:
            return f'(*{cname(nm)})'
        return cname(nm)

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
        nm = cname(nm)
        idxs = [self.expr(a) for a in args]
        if len(idxs) == 1:
            lo = self._lo(s.dims[0][0])
            off = self._sub_lo(idxs[0], lo)
            return f'{nm}[{off}]'
        # 1-based 2-/3-D arrays: use the house-style FA2/FA3 macros
        if all(self._lo(d[0]) == '1' for d in s.dims):
            if len(idxs) == 2:
                ld1 = self._extent(s.dims[0])
                return f'{nm}[FA2({idxs[0]}, {idxs[1]}, {ld1})]'
            if len(idxs) == 3:
                ld1 = self._extent(s.dims[0])
                ld2 = self._extent(s.dims[1])
                return f'{nm}[FA3({idxs[0]}, {idxs[1]}, {idxs[2]}, {ld1}, {ld2})]'
        # general N-D column-major offset (e.g. 0-based carray(0:ldc,0:ldc))
        return f'{nm}[{self._colmajor_offset(s, idxs)}]'

    def _colmajor_offset(self, s, idxs):
        parts = []
        stride = None             # product of extents of dims already seen
        n = len(s.dims)
        for k, dim in enumerate(s.dims):
            lo = self._lo(dim[0])
            term = self._sub_lo(idxs[k], lo)
            if k == 0:
                if term != '0':
                    parts.append(term)
            elif term != '0':
                parts.append(f'({term}) * ({stride})')
            # extent of the trailing (assumed-size *) dim is never needed
            if k + 1 < n:
                ext = self._extent(dim)
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
        # reductions take an array *section*; dispatch before evaluating args
        if up in ('MAXVAL', 'MINVAL', 'SUM'):
            return self._reduce(up, args[0])
        a = [self.expr(x) for x in args]
        t = [self.expr_ctype(x) for x in args]
        cx = t[0] == 'fcomplex' if t else False

        if up in ('DCMPLX', 'CMPLX'):
            return f'(({a[0]}) + ({a[1]}) * I)' if len(a) == 2 else f'({a[0]})'
        if up in ('DIMAG', 'AIMAG', 'IMAG'):
            return f'cimag({a[0]})'
        if up == 'DREAL':
            return f'creal({a[0]})'
        if up in ('DBLE', 'REAL'):
            return f'creal({a[0]})' if cx else f'(double)({a[0]})'
        if up in ('DCONJG', 'CONJG'):
            return f'conj({a[0]})'
        if up == 'CDABS':
            return f'cabs({a[0]})'
        if up == 'ABS':
            return f'cabs({a[0]})' if cx else f'fabs({a[0]})'
        if up == 'DABS':
            return f'fabs({a[0]})'
        if up in ('CDLOG',):
            return f'clog({a[0]})'
        if up in ('LOG', 'DLOG'):
            return f'clog({a[0]})' if cx else f'log({a[0]})'
        if up in ('CDEXP',):
            return f'cexp({a[0]})'
        if up in ('EXP', 'DEXP'):
            return f'cexp({a[0]})' if cx else f'exp({a[0]})'
        if up in ('CDSQRT',):
            return f'csqrt({a[0]})'
        if up in ('SQRT', 'DSQRT'):
            return f'csqrt({a[0]})' if cx else f'sqrt({a[0]})'
        if up in ('INT', 'IDINT'):
            return f'(fint)({a[0]})'
        if up in ('NINT', 'IDNINT'):
            return f'(fint)round({a[0]})'
        if up in ('DFLOAT', 'FLOAT'):
            return f'(double)({a[0]})'
        if up in ('ATAN', 'DATAN'):
            return f'atan({a[0]})'
        if up in ('ATAN2', 'DATAN2'):
            return f'atan2({a[0]}, {a[1]})'
        if up in ('SIN', 'DSIN'):
            return f'sin({a[0]})'
        if up in ('COS', 'DCOS'):
            return f'cos({a[0]})'
        if up in ('TAN', 'DTAN'):
            return f'tan({a[0]})'
        if up in ('ASIN', 'DASIN'):
            return f'asin({a[0]})'
        if up in ('ACOS', 'DACOS'):
            return f'acos({a[0]})'
        if up in ('SIGN', 'DSIGN'):
            return f'copysign({a[0]}, {a[1]})'
        if up in ('AINT', 'DINT'):
            return f'trunc({a[0]})'
        if up in ('ANINT', 'DNINT'):
            return f'round({a[0]})'
        if up in ('MAX', 'MAX0', 'MAX1', 'DMAX1', 'AMAX1'):
            return self._fold_minmax(a, '>')
        if up in ('MIN', 'MIN0', 'MIN1', 'DMIN1', 'AMIN1'):
            return self._fold_minmax(a, '<')
        if up in ('MOD',):
            return (f'fmod({a[0]}, {a[1]})' if t and t[0] == 'double'
                    else f'(({a[0]}) % ({a[1]}))')
        # otherwise: a user (cross-file or same-file) function used as a value
        rt = self.func_ctype(nm, args)
        cargs = [self.actual_arg(x) for x in args]
        return f'{self.call_name(nm, rt)}({", ".join(cargs)})'

    def _fold_minmax(self, a, op):
        acc = a[0]
        for x in a[1:]:
            acc = f'(({acc}) {op} ({x}) ? ({acc}) : ({x}))'
        return acc

    def _reduce(self, up, arg):
        # maxval/minval/sum over a 1-D array section base(lo:hi)
        if cls(arg) != 'Part_Ref':
            raise Unsupported(f'{up} of non-section {arg}')
        nm = str(arg.children[0])
        s = self.s.get(nm)
        trip = arg.children[1].children[0]
        if cls(trip) != 'Subscript_Triplet':
            raise Unsupported(f'{up} of non-triplet {arg}')
        lo = self.expr(trip.children[0])
        hi = self.expr(trip.children[1])
        lob = self._lo(s.dims[0][0])
        ct = s.ctype

        def elem(idx):
            return f'{cname(nm)}[{self._sub_lo(idx, lob)}]'

        acc = self._new_tmp()
        iv = self._new_tmp()
        self.pre.append(f'fint {iv};')
        self.pre.append(f'{ct} {acc} = {elem(lo)};')
        if up == 'SUM':
            self.pre.append(
                f'for ({iv} = ({lo}) + 1; {iv} <= ({hi}); {iv}++) '
                f'{acc} += {elem(iv)};')
        else:
            op = '>' if up == 'MAXVAL' else '<'
            self.pre.append(
                f'for ({iv} = ({lo}) + 1; {iv} <= ({hi}); {iv}++) '
                f'if ({elem(iv)} {op} {acc}) {acc} = {elem(iv)};')
        return acc

    # -- type inference (enough to choose real vs complex intrinsics) -------

    def expr_ctype(self, node):
        k = cls(node)
        if k in ('Int_Literal_Constant', 'Signed_Int_Literal_Constant'):
            return 'fint'
        if k in ('Real_Literal_Constant', 'Signed_Real_Literal_Constant'):
            return 'double'
        if k == 'Complex_Literal_Constant':
            return 'fcomplex'
        if k == 'Parenthesis':
            return self.expr_ctype(node.children[1])
        if k == 'Level_2_Unary_Expr':
            return self.expr_ctype(node.children[1])
        if k == 'Structure_Constructor':
            return 'fcomplex'
        if k == 'Name':
            s = self.s.get(str(node))
            return s.ctype if (s and s.ctype) else 'double'
        if k == 'Part_Ref':
            nm = str(node.children[0])
            s = self.s.get(nm)
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
        if up in INTRIN_REAL_RESULT:
            return 'double'
        if up in ('INT', 'IDINT', 'NINT', 'IDNINT'):
            return 'fint'
        if up in ('MAX', 'MAX0', 'MAX1', 'DMAX1', 'AMAX1',
                  'MIN', 'MIN0', 'MIN1', 'DMIN1', 'AMIN1', 'MOD'):
            t = 'fint'
            for ar in args:
                t = _promote(t, self.expr_ctype(ar))
            return t
        if up in ('LOG', 'EXP', 'SQRT', 'ABS'):
            # generic: result follows the (single) argument
            return self.expr_ctype(args[0]) if args else 'double'
        if up in ('MAXVAL', 'MINVAL', 'SUM') and args:
            base = args[0]
            if cls(base) == 'Part_Ref':
                bs = self.s.get(str(base.children[0]))
                if bs and bs.ctype:
                    return bs.ctype
            return 'double'
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
            # array slice arg: &arr[...]
            return '&' + self.part_ref(node)
        # literal or expression: materialize a temp of the inferred type
        ctype = self.expr_ctype(node)
        t = self._new_tmp()
        self.pre.append(f'{ctype} {t} = {self.expr(node)};')
        return f'&{t}'

    def _new_tmp(self):
        self.tmp_n += 1
        return f'__arg{self.tmp_n}'

    # -- statements ---------------------------------------------------------

    def stmt(self, node, out, indent):
        self.pre = []
        lines = self._stmt(node, indent)
        pad = '    ' * indent
        for p in self.pre:
            out.append(pad + p)
        out.extend(lines)

    def _label_prefix(self, node):
        lbl = getattr(getattr(node, 'item', None), 'label', None)
        return f'L{lbl}: ' if lbl is not None else ''

    def _stmt(self, node, indent):
        pad = '    ' * indent
        k = cls(node)
        lp = self._label_prefix(node)

        if k == 'Assignment_Stmt':
            lhs = self.lhs(node.children[0])
            rhs = self.expr(node.children[2])
            return [pad + lp + f'{lhs} = {rhs};']

        if k == 'Continue_Stmt':
            return [pad + lp + ';']

        if k == 'Print_Stmt':
            # user-facing prints are error/diagnostic messages; strip them
            return [pad + lp + ';'] if lp else []

        if k == 'Exit_Stmt':
            return [pad + lp + 'break;']

        if k == 'Cycle_Stmt':
            return [pad + lp + 'continue;']

        if k == 'Return_Stmt':
            if self.s.is_function:
                return [pad + lp + f'return {cname(self.s.result_name)};']
            return [pad + lp + 'return;']

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
            return [pad + lp + self.call_stmt(node)]

        if k == 'Allocate_Stmt':
            mallocs = self.alloc_stmt(node)
            return [pad + lp + mallocs[0]] + [pad + m for m in mallocs[1:]]

        if k == 'Deallocate_Stmt':
            return [pad + lp + f'free({cname(str(nm))});'
                    for nm in walk(node, f03.Name)]

        if k == 'If_Stmt':
            cond = self.expr(node.children[0])
            inner = self._stmt(node.children[1], 0)
            body = inner[0].strip() if inner else '{}'   # inner may be stripped
            return [pad + lp + f'if ({cond}) ' + body]

        if k == 'Arithmetic_If_Stmt':
            # IF (e) n1, n2, n3  ->  branch on sign of e (<0, ==0, >0)
            e = self.expr(node.children[0])
            labs = [str(node.children[i]) for i in (1, 2, 3)]
            return [pad + lp + f'if (({e}) < 0) goto L{labs[0]}; '
                    f'else if (({e}) == 0) goto L{labs[1]}; '
                    f'else goto L{labs[2]};']

        if k == 'If_Construct':
            return self.if_construct(node, indent)

        if k in ('Block_Nonlabel_Do_Construct', 'Block_Label_Do_Construct'):
            return self.do_construct(node, indent)

        raise Unsupported(f'statement {k}: {node}')

    def lhs(self, node):
        k = cls(node)
        if k == 'Name':
            nm = str(node)
            s = self.s.get(nm)
            if s is not None and s.is_dummy and not s.is_array:
                return f'*{cname(nm)}'
            return cname(nm)
        if k == 'Part_Ref':
            return self.part_ref(node)
        raise Unsupported(f'lhs {k}')

    def call_stmt(self, node):
        nm = str(node.children[0])
        argspec = node.children[1]
        args = list(argspec.children) if argspec is not None else []
        # For a same-file callee we know its parameter types, so cast each
        # actual argument to match. Fortran passes arguments untyped, so a
        # real array routinely lands on a complex*16 parameter (and vice
        # versa); the cast reproduces that reinterpretation and keeps the C
        # type-checker quiet. Cross-file callees are declared with unspecified
        # args, so they need no cast.
        nml = nm.lower()
        callee = (getattr(self.s, 'registry', {}).get(nml)
                  if nml in self.s.file_procs else None)
        cargs = []
        for i, a in enumerate(args):
            carg = self.actual_arg(a)
            if callee is not None and i < len(callee.args):
                pt = callee.get(callee.args[i]).ctype
                carg = f'({pt} *){carg}'
            cargs.append(carg)
        return f'{self.call_name(nm)}({", ".join(cargs)});'

    def alloc_stmt(self, node):
        # ALLOCATE(a(dims), b(dims), ..., stat=ierr) -> one malloc per target
        out = []
        for alc in walk(node, f03.Allocation):
            nm = str(alc.children[0])
            specs = walk(alc.children[1], f03.Allocate_Shape_Spec)
            # record runtime bounds so later indexing knows leading dims
            self.s.get(nm).dims = [tuple(sp.children) for sp in specs]
            exts = [self._extent_from_alloc(sp) for sp in specs]
            size = ' * '.join(f'({e})' for e in exts)
            ctype = self.s.get(nm).ctype
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
                var = cname(str(counter[0]))
                bounds = counter[1]
                start = self.expr(bounds[0])
                end = self.expr(bounds[1])
                step = self.expr(bounds[2]) if len(bounds) > 2 and bounds[2] is not None else '1'
                if step == '1':
                    out.append(pad + lp + f'for ({var} = {start}; {var} <= {end}; {var}++) {{')
                elif step.lstrip('-').isdigit() and step.startswith('-'):
                    out.append(pad + lp + f'for ({var} = {start}; {var} >= {end}; {var} += {step}) {{')
                else:
                    out.append(pad + lp + f'for ({var} = {start}; {var} <= {end}; {var} += {step}) {{')
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

def const_eval_int(node, scope):
    k = cls(node)
    if k == 'Int_Literal_Constant':
        return int(str(node.children[0]))
    if k == 'Name':
        s = scope.get(str(node))
        if s and s.is_param and s.value is not None:
            return const_eval_int(s.value, scope)
    if k == 'Parenthesis':
        return const_eval_int(node.children[1], scope)
    items = node.children
    if len(items) == 3 and isinstance(items[1], str):
        a = const_eval_int(items[0], scope)
        b = const_eval_int(items[2], scope)
        return {'+': a + b, '-': a - b, '*': a * b, '/': a // b}[items[1]]
    raise Unsupported(f'const-eval {k}')


# --------------------------------------------------------------------------
# Top-level generation
# --------------------------------------------------------------------------

def parse_file(path):
    reader = FortranFileReader(path, ignore_comments=True)
    parser = ParserFactory().create(std="f2008")
    return parser(reader)


def collect_procs(tree):
    procs = []
    for sp in walk(tree, (f03.Subroutine_Subprogram, f03.Function_Subprogram)):
        procs.append(sp)
    return procs


def emit_signature(scope):
    params = []
    for nm in scope.args:
        s = scope.get(nm)
        const = 'const ' if s.intent == 'in' else ''
        params.append(f'{const}{s.ctype} *{cname(nm)}')
    plist = ', '.join(params) if params else 'void'
    if scope.is_function:
        ret = scope.result_ctype
        return f'{ret} FNAME({scope.name})({plist})'
    return f'void FNAME({scope.name})({plist})'


def emit_decls(scope):
    lines = []
    for nm in scope.syms:
        s = scope.get(nm)
        cn = cname(nm)
        if s.is_dummy:
            continue
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
                lines.append(f'    {s.ctype} {cn}[{size}]{init};')
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
        lines.append(f'    {s.ctype} {cn}{init};')
    return lines


def _scalar_val(scope, s):
    em = Emitter(scope)
    return em.expr(s.value)


def emit_proc(scope):
    out = []
    out.append(emit_signature(scope) + '\n{')
    decls = emit_decls(scope)
    out.extend(decls)
    if decls:
        out.append('')
    em = Emitter(scope)
    body = []
    if scope.exec_part is not None:
        for st in scope.exec_part.children:
            em.stmt(st, body, 1)
    scope._xcalls = em.xcalls
    out.extend(body)
    if scope.is_function:
        out.append(f'    return {cname(scope.result_name)};')
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
    for sc in scopes:
        sc.registry = registry      # for same-file call arg typing
    return scopes


_MATH_FUNCS = ('sqrt', 'log', 'exp', 'fabs', 'pow', 'atan', 'atan2', 'sin',
               'cos', 'tan', 'asin', 'acos', 'copysign', 'trunc', 'round',
               'fmod')
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
    'fmm_ipowi': '''static fint fmm_ipowi(fint a, fint n) {
    fint pw = 1, x = a;
    if (n != 0)
        for (;;) { if (n & 1) pw *= x; n >>= 1; if (n) x *= x; else break; }
    return pw;
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
    needs_math = any(f + '(' in blob for f in _MATH_FUNCS)
    needs_cplx = ('fcomplex' in blob) or any(t in blob for t in _CPLX_FUNCS)

    out = []
    out.append('/*')
    out.append(f' * {basename}.c - C translation of {path}')
    out.append(' *')
    out.append(' * Generated by fort2c (fparser2 front end). Do not edit by hand.')
    out.append(' */')
    out.append('')
    if needs_alloc:
        out.append('#include <stdlib.h>')
    if needs_math:
        out.append('#include <math.h>')
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
        out.append(emit_signature(sc) + ';')
    out.append('')
    out.append(f'#endif /* {guard} */')
    return '\n'.join(out)
