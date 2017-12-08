"""Fortran unparsing."""

import ast
import collections.abc
import copy
import io
import logging

from astunparse.unparser import INFSTR
import horast
import typed_ast.ast3 as typed_ast3
import typed_astunparse
from typed_astunparse.unparser import interleave

from ..general import Language, Unparser
from .ast_generalizer import FORTRAN_PYTHON_TYPE_PAIRS, PYTHON_TYPE_ALIASES

_LOG = logging.getLogger(__name__)

PYTHON_FORTRAN_TYPE_PAIRS = {value: key for key, value in FORTRAN_PYTHON_TYPE_PAIRS.items()}

for _name, _aliases in PYTHON_TYPE_ALIASES.items():
    for _alias in _aliases:
        PYTHON_FORTRAN_TYPE_PAIRS[_alias] = PYTHON_FORTRAN_TYPE_PAIRS[_name]


def _transform_print_call(call):
    if not hasattr(call, 'fortran_metadata'):
        call.fortran_metadata = {}
    call.fortran_metadata['is_transformed'] = True
    if len(call.args) == 1:
        arg = call.args[0]
        if isinstance(arg, typed_ast3.Call) and isinstance(arg.func, typed_ast3.Attribute):
            label = int(arg.func.value.id.replace('format_label_', ''))
            call.args = [typed_ast3.Num(n=label)] + arg.args
            return call
    call.args.insert(0, typed_ast3.Ellipsis())
    return call


PYTHON_FORTRAN_INTRINSICS = {
    'np.argmin': 'minloc',
    'np.argmax': 'maxloc',
    'np.array': lambda _: _.args[0],
    'np.dot': 'dot_product',
    'np.maximum': 'max',
    'np.minimum': 'min',
    'np.sqrt': 'sqrt',
    'np.zeros': lambda _: typed_ast3.Num(n=0),
    'print': _transform_print_call
    }


def _match_subscripted_attributed_name(tree, name: str, attr: str) -> bool:
    return isinstance(tree, typed_ast3.Subscript) \
        and isinstance(tree.value, typed_ast3.Attribute) \
        and isinstance(tree.value.value, typed_ast3.Name) \
        and tree.value.value.id == name and tree.value.attr == attr


def _match_array(tree) -> bool:
    return _match_subscripted_attributed_name(tree, 'st', 'ndarray')


def _match_io(tree) -> bool:
    return _match_subscripted_attributed_name(tree, 't', 'IO')


class Fortran77UnparserBackend(horast.unparser.Unparser):

    """Implementation of Fortran 77 unparser."""

    lang_name = 'Fortran 77'

    def __init__(self, *args, indent: int = 2, fixed_form: bool = True, **kwargs):
        self._indent_level = indent
        self._fixed_form = fixed_form
        self._line_len = 0
        self._max_line_len = 72
        super().__init__(*args, **kwargs)

    def fill(self, text='', continuation: bool = False):
        self.write('\n')
        if self._fixed_form:
            self.write('      ')
            self.write('+' if continuation else ' ')
        self.write(' ' * (self._indent_level * self._indent))
        self.write(text)

    def write(self, text):
        if text == '\n':
            self._line_len = 0
        elif '\n' in text:
            raise NotImplementedError('long text printing not yet implemented')
        if not self._fixed_form:
            raise NotImplementedError('free-form not yet implemented')
        if self._fixed_form:
            if self._max_line_len is not None and self._line_len + len(text) > self._max_line_len:
                self.fill('', continuation=True)
        super().write(text)
        self._line_len += len(text)

    def enter(self):
        self._indent += 1

    def _unsupported_syntax(self, tree):
        unparsed = 'invalid'
        try:
            unparsed = '"""{}"""'.format(typed_astunparse.unparse(tree).strip())
        except AttributeError:
            pass
        self.fill('unsupported_syntax')
        raise SyntaxError('unparsing {} like """{}""" ({} in Python) is unsupported for {}'.format(
            tree.__class__.__name__, typed_ast3.dump(tree), unparsed, self.lang_name))

    def dispatch_var_type(self, tree):
        code = horast.unparse(tree)
        stripped_code = code.strip()
        if stripped_code in PYTHON_FORTRAN_TYPE_PAIRS:
            type_name, precision = PYTHON_FORTRAN_TYPE_PAIRS[stripped_code]
            self.write(type_name)
            if precision is not None:
                self.write('*')
                self.write(str(precision))
        elif _match_array(tree):
            sli = tree.slice
            assert isinstance(sli, typed_ast3.Index), typed_astunparse.dump(tree)
            assert isinstance(sli.value, typed_ast3.Tuple)
            assert len(sli.value.elts) == 3
            elts = sli.value.elts
            self.dispatch_var_type(elts[1])
            self.write(', ')
            self.write('dimension(')
            self.dispatch(elts[2])
            self.write(')')
        elif _match_io(tree):
            self.write('integer')
        else:
            raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(tree)}')
            #self._unsupported_syntax(tree)
            #self.dispatch(tree)

    def dispatch_for_iter(self, tree):
        if not isinstance(tree, typed_ast3.Call) \
                or not isinstance(tree.func, typed_ast3.Name) or tree.func.id != 'range' \
                or len(tree.args) not in (1, 2, 3):
            self._unsupported_syntax(tree)
        if len(tree.args) == 1:
            lower = typed_ast3.Num(n=0)
            upper = tree.args[0]
            step = None
        else:
            lower, upper, step, *_ = tree.args + [None, None]
        self.dispatch(lower)
        self.write(', ')
        if isinstance(upper, typed_ast3.BinOp) and isinstance(upper.op, typed_ast3.Add) \
                and isinstance(upper.right, typed_ast3.Num) and upper.right.n == 1:
            self.dispatch(upper.left)
        else:
            self.dispatch(typed_ast3.BinOp(left=upper, op=typed_ast3.Sub(),
                                           right=typed_ast3.Num(n=1)))
        if step is not None:
            self.write(', ')
            self.dispatch(step)

    def _Import(self, t):
        names = [name for name in t.names
                 if name.name not in ('numpy', 'static_typing', 'typing')]
        if not names:
            return
        self.fill('use ')
        #print([typed_astunparse.unparse(_) for _ in names])
        interleave(lambda: self.write(', '), self.dispatch, names)

    def _ImportFrom(self, t):
        if t.level > 0:
            self._unsupported_syntax(t)
        self.fill('use ')
        self.write(t.module)
        self.write(', only : ')
        interleave(lambda: self.write(', '), self.dispatch, t.names)

    def _Assign(self, t):
        metadata = getattr(t, 'fortran_metadata', {})
        if not metadata:
            return super()._Assign(t)
        if metadata.get('is_allocation', False):
            self.fill('allocate(')
            for i, target in enumerate(t.targets):
                if i > 0:
                    self.write(', ')
                self.dispatch(target)
                self.write('(')
                self.dispatch(t.value.args[0])
                self.write(')')
            self.write(')')
            return
        raise NotImplementedError('Assign with Fortran metadata')

    def _AugAssign(self, t):
        self.fill()
        self.dispatch(t.target)
        self.write(" "+self.binop[t.op.__class__.__name__]+"= ")
        self.dispatch(t.value)

    def _AnnAssign(self, t):
        if isinstance(t.annotation, typed_ast3.NameConstant):
            assert t.annotation.value == None
            assert isinstance(t.target, typed_ast3.Name)
            assert t.target.id == 'implicit'
            self.fill()
            self.write('implicit none')
            return
        self.fill()
        metadata = getattr(t, 'fortran_metadata', {})
        if metadata.get('is_format', False):
            self.write(t.target.id.replace('format_label_', ''))
            self.write(' ')
            self.dispatch(t.value)
            return
        if metadata.get('is_allocation', False):
            raise NotImplementedError('allocation')
        self.dispatch_var_type(t.annotation)
        for key, value in metadata.items():
            self.write(', ')
            if value is True:
                self.write(key[3:])
            else:
                self.write(key)
                self.write('(')
                self.write(value)
                self.write(')')
        self.write(' :: ')
        self.dispatch(t.target)
        if t.value:
            self.write(" = ")
            self.dispatch(t.value)

    #def _Return(self, t):
    #    self.fill("return")
    #    if t.value:
    #        self.write(" ")
    #        self.dispatch(t.value)

    def _Pass(self, t):
        self.fill('continue')

    def _Break(self, t):
        self.fill('exit')

    def _Continue(self, t):
        self.fill('cycle')

    def _Delete(self, t):
        self.fill('deallocate (')
        interleave(lambda: self.write(', '), self.dispatch, t.targets)
        self.write(')')

    def _Assert(self, t):
        raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    def _Exec(self, t):
        raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    def _Print(self, t):
        raise NotImplementedError('old Python AST is not supported')

    def _Global(self, t):
        self._unsupported_syntax(t)

    def _Nonlocal(self, t):
        self._unsupported_syntax(t)

    def _Yield(self, t):
        self._unsupported_syntax(t)

    def _YieldFrom(self, t):
        self._unsupported_syntax(t)

    def _Raise(self, t):
        self._unsupported_syntax(t)

    def _Try(self, t):
        self._unsupported_syntax(t)

    def _TryExcept(self, t):
        raise NotImplementedError('old Python AST is not supported')

    def _TryFinally(self, t):
        raise NotImplementedError('old Python AST is not supported')

    def _ExceptHandler(self, t):
        self._unsupported_syntax(t)

    def _ClassDef(self, t):
        self._unsupported_syntax(t)

    def _FunctionDef(self, t):
        self.write('\n')
        if t.decorator_list:
            self._unsupported_syntax(t)
        self.fill('subroutine ' + t.name + ' (')
        self.dispatch(t.args)
        self.write(')')
        if t.returns:
            _LOG.warning('skipping return')
            #raise NotImplementedError('not yet implemented: {}'.format(typed_astunparse.dump(t)))
            #self.write(' result ')
            #self.dispatch(t.returns)
        self.enter()
        self.dispatch(t.body)
        self.leave()
        self.fill('end subroutine ' + t.name)

    def _AsyncFunctionDef(self, t):
        self._unsupported_syntax(t)

    def _For(self, t):
        if hasattr(t, 'type_comment') and t.type_comment or t.orelse:
            self._unsupported_syntax(t)

        self.fill('do ')
        self.dispatch(t.target)
        self.write(" = ")
        self.dispatch_for_iter(t.iter)
        self.enter()
        self.dispatch(t.body)
        self.leave()
        self.fill('end do')

    def _AsyncFor(self, t):
        self._unsupported_syntax(t)

    def _If(self, t):
        return self._generic_If(t)

    def _generic_If(self, t, prefix='if'):
        self.fill('{} ('.format(prefix))
        self.dispatch(t.test)
        self.write(') then')
        self.enter()
        self.dispatch(t.body)
        self.leave()
        if t.orelse:
            if len(t.orelse) == 1 and isinstance(t.orelse[0], typed_ast3.If):
                return self._generic_If(t.orelse[0], 'else if')
            else:
                self.fill('else')
                self.enter()
                self.dispatch(t.orelse)
                self.leave()
        self.fill('end if')

    def _While(self, t):
        raise NotImplementedError('not yet implemented: {}'.format(typed_astunparse.dump(t)))

    def _With(self, t):
        self._unsupported_syntax(t)

    def _AsyncWith(self, t):
        self._unsupported_syntax(t)

    # expr
    def _Bytes(self, t):
        self.write(repr(t.s))

    def _Str(self, tree):
        self.write(repr(tree.s))

    def _FormattedValue(self, t):
        if t.conversion != -1 or t.format_spec != None:
            self._unsupported_syntax(t)
        self.dispatch(t.value)

    def _JoinedStr(self, t):
        self.write('format(')
        interleave(lambda: self.write(', '), self.dispatch, t.values)
        self.write(')')

    def _Name(self, t):
        self.write(t.id)

    def _NameConstant(self, t):
        if t.value is None:
            _LOG.error('unparsing invalid Fortran from """%s"""', typed_astunparse.dump(t))
        self.write({
            None: '.none.',
            False: '.false.',
            True: '.true.'}[t.value])

    def _Repr(self, t):
        self._unsupported_syntax(t)

    def _Num(self, t):
        repr_n = repr(t.n)
        self.write(repr_n.replace("inf", INFSTR))

    def _List(self, t):
        self.write('/ ')
        interleave(lambda: self.write(", "), self.dispatch, t.elts)
        self.write(' /')

    def _ListComp(self, t):
        self.write('(')
        self.dispatch(t.elt)
        self.write(', ')
        for gen in t.generators:
            self.dispatch(gen)
        self.write(')')
        # raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    def _GeneratorExp(self, t):
        self._unsupported_syntax(t)

    def _SetComp(self, t):
        self._unsupported_syntax(t)

    def _DictComp(self, t):
        self._unsupported_syntax(t)

    def _comprehension(self, t):
        if getattr(t, 'is_async', False) or t.ifs:
            self._unsupported_syntax(t)
        self.dispatch(t.target)
        self.write(' = ')
        self.dispatch_for_iter(t.iter)
        # raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    def _IfExp(self, t):
        raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    def _Set(self, t):
        self._unsupported_syntax(t)

    def _Dict(self, t):
        self._unsupported_syntax(t)

    def _Tuple(self, t):
        interleave(lambda: self.write(', '), self.dispatch, t.elts)

    unop = {'Invert': '~', 'Not': '.not.', 'UAdd': '+', 'USub': '-'}

    def _UnaryOp(self, t):
        self.write('(')
        self.write(self.unop[t.op.__class__.__name__])
        self.write(' ')
        self.dispatch(t.operand)
        self.write(')')
        # raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')

    binop = {
        'Add': '+', 'Sub': '-', 'Mult': '*', 'Div': '/',  # 'Mod': '%',
        # 'LShift': '<<', 'RShift': '>>', 'BitOr': '|', 'BitXor': '^', 'BitAnd': '&',
        'FloorDiv': '/', 'Pow': '**'}

    def _BinOp(self, t):
        if t.op.__class__.__name__ not in self.binop:
            raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        self.write('(')
        self.dispatch(t.left)
        self.write(' ' + self.binop[t.op.__class__.__name__] + ' ')
        self.dispatch(t.right)
        self.write(')')

    cmpops = {
        'Eq': '==', 'NotEq': '<>', 'Lt': '<', 'LtE': '<=', 'Gt': '>', 'GtE': '>='}
        #'Is': '===', 'IsNot': 'is not'}
    def _Compare(self, t):
        if len(t.ops) > 1 or len(t.comparators) > 1 \
                or any([o.__class__.__name__ not in self.cmpops for o in t.ops]):
            raise NotImplementedError('not yet implemented: {}'.format(typed_astunparse.dump(t)))
        super()._Compare(t)

    boolops = {ast.And: '.and.', ast.Or: '.or.', typed_ast3.And: '.and.', typed_ast3.Or: '.or.'}
    def _BoolOp(self, t):
        self.write("(")
        s = " %s " % self.boolops[t.op.__class__]
        interleave(lambda: self.write(s), self.dispatch, t.values)
        self.write(")")

    def _Attribute(self,t):
        code = typed_astunparse.unparse(t).strip()
        if code == 't.IO':
            self.write('integer')
        elif code == 'Fortran.file_handles':
            pass
        else:
            self._unsupported_syntax(t)

    def _Call(self, t):
        if getattr(t, 'fortran_metadata', {}).get('is_procedure_call', False):
            self.write('call ')
        func_name = horast.unparse(t.func).strip()
        if func_name in PYTHON_FORTRAN_INTRINSICS and not getattr(t, 'fortran_metadata', {}).get('is_transformed', False):
            new_func = PYTHON_FORTRAN_INTRINSICS[func_name]
            if isinstance(new_func, collections.abc.Callable):
                self.dispatch(new_func(t))
                return
            t = copy.copy(t)
            t.func = typed_ast3.Name(id=new_func)
        elif func_name.startswith('np.'):
            raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        super()._Call(t)
    #    self.dispatch(t.func)
    #    self.write("(")
    #    comma = False
    #    for e in t.args:
    #        if comma: self.write(", ")
    #        else: comma = True
    #        self.dispatch(e)
    #    for e in t.keywords:
    #        if comma: self.write(", ")
    #        else: comma = True
    #        self.dispatch(e)
    #    if sys.version_info[:2] < (3, 5):
    #        if t.starargs:
    #            if comma: self.write(", ")
    #            else: comma = True
    #            self.write("*")
    #            self.dispatch(t.starargs)
    #        if t.kwargs:
    #            if comma: self.write(", ")
    #            else: comma = True
    #            self.write("**")
    #            self.dispatch(t.kwargs)
    #    self.write(")")

    def _Subscript(self, t):
        self.dispatch(t.value)
        self.write("(")
        self.dispatch(t.slice)
        #if isinstance(t.slice, typed_ast3.Index):
        #elif isinstance(t.slice, typed_ast3.Slice):
        #    raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        #elif isinstance(t.slice, typed_ast3.ExtSlice):
        #    raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        #else:
        #    raise ValueError()
        self.write(")")

    def _Starred(self, t):
        self._unsupported_syntax(t)

    # slice
    def _Ellipsis(self, t):
        #self._unsupported_syntax(t)
        _LOG.warning('special usage of Ellipsis')
        self.write('*')

    def _Index(self, t):
        self.dispatch(t.value)

    def _Slice(self, t):
        if t.lower:
            self.dispatch(t.lower)
        self.write(":")
        if t.upper:
            self.dispatch(t.upper)
        if t.step:
            self.write(":")
            self.dispatch(t.step)

    def _ExtSlice(self, t):
        interleave(lambda: self.write(', '), self.dispatch, t.dims)

    # argument
    def _arg(self, t):
        if t.annotation:
            self._unsupported_syntax(t)
        self.write(t.arg)

    # others
    def _arguments(self, t):
        if t.vararg or t.kwonlyargs or t.kw_defaults or t.kwarg or t.defaults:
            raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        super()._arguments(t)

    def _keyword(self, t):
        if t.arg is None:
            raise NotImplementedError(f'not yet implemented: {typed_astunparse.dump(t)}')
        super()._keyword(t)

    def _Lambda(self, t):
        self._unsupported_syntax(t)

    def _alias(self, t):
        if t.asname:
            self._unsupported_syntax(t)
            return
        self.write(t.name)

    def _withitem(self, t):
        self._unsupported_syntax(t)

    def _Await(self, t):
        self._unsupported_syntax(t)

    def _Comment(self, node):
        if node.value.s.startswith(' Fortran metadata:'):
            return
        metadata = getattr(node, 'fortran_metadata', {})
        _max_line_len = self._max_line_len
        self._max_line_len = None
        if metadata.get('is_directive', False):
            _indent = self._indent
            self._indent = 0
            super().fill('#')
            self._indent = _indent
        else:
            if node.eol:
                self.write('  !')
            else:
                self.fill('!')
        self.write(node.value.s)
        self._max_line_len = _max_line_len


class Fortran77Unparser(Unparser):

    def __init__(self):
        super().__init__(Language.find('Fortran 77'))

    def unparse(self, tree, indent: int = 2, fixed_form: bool = True) -> str:
        stream = io.StringIO()
        Fortran77UnparserBackend(tree, file=stream, indent=indent, fixed_form=fixed_form)
        return stream.getvalue()


class Fortran2008Unparser(Unparser):

    def __init__(self):
        super().__init__(Language.find('Fortran 2008'))

    def unparse(self, tree, indent: int = 4, fixed_form: bool = False) -> str:
        raise NotImplementedError('not yet implemented')
