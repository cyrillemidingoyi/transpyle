"""Integration tests based on various scientific applications."""

import logging
import os
import pathlib
import unittest
import xml.etree.ElementTree as ET

import static_typing as st
import typed_ast.ast3 as typed_ast3
import typed_astunparse

from transpyle.general import Language, CodeReader, Parser, AstGeneralizer, Unparser, CodeWriter
from transpyle.python.transformations import inline_syntax
from transpyle.pair import replace_scope

from test.common import \
    RESULTS_ROOT, APPS_RESULTS_ROOT, basic_check_fortran_code, basic_check_fortran_ast, \
    basic_check_python_code, basic_check_python_ast

_LOG = logging.getLogger(__name__)

_HERE = pathlib.Path(__file__).resolve().parent

_ROOT = _HERE.parent.parent

_APPS_ROOT = pathlib.Path(os.environ.get('TEST_APPS_ROOT', _ROOT.parent)).resolve()

_FORTRAN_SUFFIXES = ('.f', '.F', '.f90', '.F90')

# _APPS_PARENT_PATHS = {
#     'miranda_io': pathlib.Path('fortran'),
#     'FLASH-4.5': pathlib.Path('fortran'),
#     'FLASH-SUBSET': pathlib.Path('fortran'),
#     'FLASH-SUBSET-hydro': pathlib.Path('fortran'),
#     'FFB-MINI': pathlib.Path('fortran')}

_APPS_ROOT_PATHS = {
    'miranda_io': pathlib.Path('miranda_io'),
    'FLASH-4.5': pathlib.Path('flash-4.5'),
    'FLASH-SUBSET': pathlib.Path('flash-subset', 'FLASH4.4'),
    'FLASH-SUBSET-hydro': pathlib.Path('flash-subset', 'FLASH4.4'),
    'FFB-MINI': pathlib.Path('ffb-mini')}

_APPS_OPTIONAL = {'FLASH-4.5', 'FLASH-SUBSET', 'FLASH-SUBSET-hydro'}

# _APPS_ROOT_PATHS = {
#     app: (pathlib.Path('..', path) if _APPS_ROOT.joinpath('..', path).is_dir()
#           else pathlib.Path('..', '..', _APPS_PARENT_PATHS[app], path))
#     for app, path in _APPS_ROOT_PATHS.items()}

_APPS_ROOT_PATHS = {
    app: _APPS_ROOT.joinpath(path).resolve() for app, path in _APPS_ROOT_PATHS.items()
    if app not in _APPS_OPTIONAL or _APPS_ROOT.joinpath(path).is_dir()}

_FLASH_COMMON_PATHS = [
    'physics/Hydro/HydroMain/split/MHD_8Wave/hy_8wv_interpolate.F90',
    'physics/Hydro/HydroMain/split/MHD_8Wave/hy_8wv_fluxes.F90',
    'physics/Eos/EosMain/Gamma/eos_idealGamma.F90',
    'physics/Hydro/HydroMain/split/MHD_8Wave/hy_8wv_sweep.F90',
    # 'physics/Hydro/HydroMain/unsplit/hy_uhd_TVDslope.F90'  # interface
    ]

_APPS_CODE_FILEPATHS = {
    'miranda_io': [pathlib.Path(_APPS_ROOT_PATHS['miranda_io'], 'miranda_io.f90')],
    'FLASH-4.5': [
        pathlib.Path(_APPS_ROOT_PATHS['FLASH-4.5'], 'source', pathlib.Path(input_path))
        for input_path in [
            'physics/Hydro/HydroMain/unsplit/hy_uhd_getFaceFlux.F90',
            'physics/Hydro/HydroMain/unsplit/hy_uhd_DataReconstructNormalDir_MH.F90',
            'physics/Hydro/HydroMain/unsplit/hy_uhd_upwindTransverseFlux.F90',
            'physics/Hydro/HydroMain/unsplit/hy_uhd_Roe.F90'
            ] + _FLASH_COMMON_PATHS] if 'FLASH-4.5' in _APPS_ROOT_PATHS else [],
    'FLASH-SUBSET': [
        pathlib.Path(_APPS_ROOT_PATHS['FLASH-SUBSET'], 'source', pathlib.Path(input_path))
        for input_path in [
            'physics/Hydro/HydroMain/unsplit/hy_getFaceFlux.F90',
            'physics/Hydro/HydroMain/simpleUnsplit/HLL/hy_hllUnsplit.F90',
            'physics/Hydro/HydroMain/unsplit/hy_DataReconstructNormalDir_MH.F90',
            'physics/Hydro/HydroMain/unsplit/hy_upwindTransverseFlux.F90',
            'physics/Hydro/HydroMain/unsplit/hy_TVDslope.F90',  # also in 4.5, but fails
            'physics/Hydro/HydroMain/unsplit/hy_Roe.F90'
            ] + _FLASH_COMMON_PATHS] if 'FLASH-SUBSET' in _APPS_ROOT_PATHS else [],
    'FLASH-SUBSET-hydro': [
        pathlib.Path(_APPS_ROOT_PATHS['FLASH-SUBSET'], 'source', pathlib.Path(input_path))
        for input_path in [
            # 'physics/Hydro/HydroMain/simpleUnsplit/HLL/hy_hllUnsplit.F90'
            'physics/Hydro/HydroMain/unsplit/hy_upwindTransverseFlux_loop.F90'
            ]] if 'FLASH-SUBSET' in _APPS_ROOT_PATHS else [],
    'FFB-MINI': [
        pathlib.Path(root, name)
        for root, _, files in os.walk(str(pathlib.Path(_APPS_ROOT_PATHS['FFB-MINI'], 'src')))
        for name in files if pathlib.Path(name).suffix in _FORTRAN_SUFFIXES and name not in {
            'ddcom4.F',  # SyntaxError - just not implemented yet
            'ffb_mini_main.F90',  # NotImplementedError
            'f_test.F90',  # NotImplementedError
            'mod_maprof.F90',  # NotImplementedError
            # OFP fails for the following files
            # issues need to be resolved upstream or files need to be modified
            'bcgs3x.F', 'bcgsxe.F', 'calax3.F', 'callap.F', 'dd_mpi.F', 'e2plst.F', 'extrfn.F',
            'gfutil.f', 'grad3x.F', 'les3x.F', 'lesrop.F', 'lesrpx.F', 'lessfx.F', 'lrfnms.F',
            'makemesh.F90', 'miniapp_util.F', 'mfname.F', 'neibr2.F', 'nodlex.F', 'pres3e.F',
            'rcmelm.F', 'rfname.F', 'srfexx.F', 'vel3d1.F', 'vel3d2.F'}]}


def _prepare_roundtrip(case, language: Language):
    parser = Parser.find(language)()
    case.assertIsInstance(parser, Parser)
    ast_generalizer = AstGeneralizer.find(language)()
    case.assertIsInstance(ast_generalizer, AstGeneralizer)
    unparser = Unparser.find(language)()
    case.assertIsInstance(unparser, Unparser)
    return parser, ast_generalizer, unparser


def _roundtrip_fortran(case, path, results_path, parser, ast_generalizer, unparser):
    with open(str(path)) as original_file:
        basic_check_fortran_code(case, path, original_file.read(), results=results_path,
                                 append_suffix=False)
    fortran_ast = parser.parse('', path)
    basic_check_fortran_ast(case, path, fortran_ast, results=results_path)
    tree = ast_generalizer.generalize(fortran_ast)
    basic_check_python_ast(case, path, tree, results=results_path)
    # python_code = python_unparser.unparse(tree)
    # basic_check_python_code(self, path, python_code, results=results_path)
    # tree = python_parser.parse(python_code)
    # basic_check_python_ast(self, path, tree, results=results_path)
    fortran_code = unparser.unparse(tree)
    basic_check_fortran_code(case, path, fortran_code, results=results_path)


def _migrate_fortran(case, path, results_path, parser, ast_generalizer, unparser):
    with open(str(path)) as original_file:
        basic_check_fortran_code(case, path, original_file.read(), results=results_path,
                                 append_suffix=False)
    fortran_ast = parser.parse('', path)
    basic_check_fortran_ast(case, path, fortran_ast, results=results_path)
    tree = ast_generalizer.generalize(fortran_ast)
    basic_check_python_ast(case, path, tree, results=results_path)
    python_code = unparser.unparse(tree)
    basic_check_python_code(case, path, python_code, results=results_path)


class Tests(unittest.TestCase):

    def _test_app(self, app_name, tools, test, dir_name=None):
        if app_name not in _APPS_ROOT_PATHS and app_name in _APPS_OPTIONAL:
            self.skipTest('{} directory not found'.format(app_name))
        if dir_name is None:
            dir_name = app_name.lower()
        results_path = pathlib.Path(APPS_RESULTS_ROOT, dir_name)
        results_path.mkdir(exist_ok=True)
        self.assertGreater(len(_APPS_CODE_FILEPATHS[app_name]), 0, msg=_APPS_ROOT_PATHS[app_name])
        for path in _APPS_CODE_FILEPATHS[app_name]:
            with self.subTest(path=path):
                test(self, path, results_path, *tools)

    def test_roundtrip_miranda_io(self):
        self._test_app('miranda_io', _prepare_roundtrip(self, Language.find('Fortran')),
                       _roundtrip_fortran)

    @unittest.skipUnless(os.environ.get('TEST_FLASH'), 'skipping test on FLASH code')
    def test_roundtrip_flash_45(self):
        self._test_app('FLASH-4.5', _prepare_roundtrip(self, Language.find('Fortran')),
                       _roundtrip_fortran)

    @unittest.skipUnless(os.environ.get('TEST_FLASH'), 'skipping test on FLASH code')
    def test_roundtrip_flash_subset(self):
        self._test_app('FLASH-SUBSET', _prepare_roundtrip(self, Language.find('Fortran')),
                       _roundtrip_fortran)

    @unittest.skipUnless(os.environ.get('TEST_FLASH'), 'skipping test on FLASH code')
    def test_roundtrip_flash_subset_hydro(self):
        self._test_app('FLASH-SUBSET-hydro', _prepare_roundtrip(self, Language.find('Fortran')),
                       _roundtrip_fortran)

    @unittest.skipUnless(os.environ.get('TEST_FLASH'), 'skipping test on FLASH code')
    def test_inline_flash_subset_hydro(self):
        app_name = 'FLASH-SUBSET'
        if app_name not in _APPS_ROOT_PATHS and app_name in _APPS_OPTIONAL:
            self.skipTest('{} directory not found'.format(app_name))
        language = Language.find('Fortran')
        reader = CodeReader()
        parser = Parser.find(language)()
        ast_generalizer = AstGeneralizer.find(language)()
        f_unparser = Unparser.find(language)()
        py_unparser = Unparser.find(Language.find('Python'))()
        writer = CodeWriter()

        dir_name = app_name.lower()
        results_path = pathlib.Path(RESULTS_ROOT, 'transformations', 'inlining', dir_name)
        results_path.mkdir(parents=True, exist_ok=True)

        path_pairs = [
            (pathlib.Path('physics/Hydro/HydroMain/unsplit/hy_upwindTransverseFlux_loop.F90'),
             pathlib.Path('physics/Hydro/HydroMain/unsplit/hy_upwindTransverseFlux.F90'),
             (1, 1)),
            (pathlib.Path('physics/Eos/EosMain/Eos_getData_loop1.F90'),
             pathlib.Path('physics/Eos/EosMain/Eos_getData.F90'),
             (1, 2))]

        for inlined_path, target_path, (index, extra_lines) in path_pairs:
            inlined_path = pathlib.Path(_APPS_ROOT_PATHS[app_name], 'source', inlined_path)
            target_path = pathlib.Path(_APPS_ROOT_PATHS[app_name], 'source', target_path)

            output_inlined_path = results_path.joinpath(inlined_path.name)
            output_target_path = results_path.joinpath(target_path.name)
            output_path = results_path.joinpath(target_path.with_suffix('').name + '_inlined.F90')

            inlined_xml = parser.parse('', inlined_path)
            inlined_xml = inlined_xml.find('.//subroutine')
            writer.write_file(ET.tostring(inlined_xml, 'utf-8').decode(),
                              output_inlined_path.with_suffix('.xml'))

            inlined_syntax = ast_generalizer.generalize(inlined_xml)
            writer.write_file(typed_astunparse.dump(inlined_syntax),
                              output_inlined_path.with_suffix('.ast.py'))
            writer.write_file(py_unparser.unparse(inlined_syntax),
                              output_inlined_path.with_suffix('.py'))
            writer.write_file(f_unparser.unparse(inlined_syntax),
                              output_inlined_path.with_suffix('.f95'))

            target_code = reader.read_file(target_path)
            target_xml = parser.parse(target_code, target_path)
            # import ipdb; ipdb.set_trace()
            target_xml = target_xml.findall('.//call')[index]
            writer.write_file(ET.tostring(target_xml, 'utf-8').decode(),
                              output_target_path.with_suffix('.xml'))

            target_syntax = ast_generalizer.generalize(target_xml)
            writer.write_file(typed_astunparse.dump(target_syntax),
                              output_target_path.with_suffix('.ast.py'))
            writer.write_file(py_unparser.unparse(target_syntax),
                              output_target_path.with_suffix('.py'))
            writer.write_file(f_unparser.unparse(target_syntax),
                              output_target_path.with_suffix('.f95'))

            mock_function = typed_ast3.FunctionDef(
                'f', typed_ast3.arguments([], None, [], None, [], []),
                [typed_ast3.Expr(target_syntax)], [], None, None)
            output_syntax = inline_syntax(mock_function, inlined_syntax, globals_=globals())
            output_syntax = st.augment(typed_ast3.Module(output_syntax.body, []), eval_=False)
            writer.write_file(typed_astunparse.dump(output_syntax),
                              output_path.with_suffix('.ast.py'))
            writer.write_file(py_unparser.unparse(output_syntax),
                              output_path.with_suffix('.py'))
            output_code = f_unparser.unparse(output_syntax)
            writer.write_file(output_code, output_path.with_suffix('.f95'))

            _LOG.warning('[%s %s] <- %i', target_xml.attrib['line_begin'],
                         target_xml.attrib['line_end'], len(output_code))
            total_code = replace_scope(
                target_code, int(target_xml.attrib['line_begin']),
                int(target_xml.attrib['line_end']) + extra_lines, output_code)
            writer.write_file(total_code, output_path)

    @unittest.skipUnless(os.environ.get('TEST_LONG'), 'skipping long test')
    def test_roundtrip_ffbmini(self):
        self._test_app('FFB-MINI', _prepare_roundtrip(self, Language.find('Fortran')),
                       _roundtrip_fortran)
