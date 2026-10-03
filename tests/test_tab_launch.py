import ast
import subprocess
import sys
import unittest
from pathlib import Path

class LaunchDirectoryTests(unittest.TestCase):
    def test_package_imports_follow_repository_bootstrap(self):
        source=Path('gbop_voice_web/server.py').read_text()
        tree=ast.parse(source)
        bootstrap=next(n.lineno for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='sys.path.insert')
        self.assertTrue(all(n.lineno>bootstrap for n in tree.body if isinstance(n,ast.ImportFrom) and (n.module or '').startswith('gbop_voice_web')))
    def test_watch_import_from_isolated_render_working_directory(self):
        path=Path('gbop_voice_web/server.py').resolve()
        source=path.read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ImportFrom) and n.module=='gbop_voice_web.market_watch')
        prefix='\n'.join(source.splitlines()[:node.end_lineno])
        result=subprocess.run([sys.executable,'-I','-c','__file__='+repr(str(path))+'\n'+prefix],cwd=path.parent,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
