"""Setup script for transpyle package."""

import setup_boilerplate


class Package(setup_boilerplate.Package):

    """Package metadata."""

    name = 'transpyle'
    description = 'performance-oriented transpiler for Python'
    download_url = 'https://github.com/mbdevpl/transpyle'
    classifiers = [
        'Development Status :: 1 - Planning',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: Apache Software License',
        'Natural Language :: English',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Education',
        'Topic :: Scientific/Engineering',
        'Topic :: Software Development :: Code Generators',
        'Topic :: Software Development :: Compilers',
        'Topic :: Software Development :: Pre-processors',
        'Topic :: Utilities']
    keywords = ['compiler', 'just-in-time', 'source-to-source', 'transpilation', 'transpiler']
    extras_require = {
        'all': ['cython', 'nuitka', 'numpy'],
        'c': ['cython'],
        'cpp': ['nuitka'],
        'cython': ['cython'],
        'fortran': ['numpy'],
        'opencl': ['pyopencl']}
    entry_points = {
        'console_scripts': ['transpyle = transpyle.__main__:main']}


if __name__ == '__main__':
    Package.setup()