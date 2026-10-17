"""The reusable distribution must import without installing the product app."""

import subprocess
import sys

from tests.fixtures.independence import check_independence


def test_core_imports_without_any_bundled_plugin():
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import pkgutil
import sys

class PluginsUnavailable(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'the_framework.plugins' or fullname.startswith('the_framework.plugins.'):
            raise ImportError('plugin unavailable: ' + fullname)

sys.meta_path.insert(0, PluginsUnavailable())
import the_framework
for root in ('the_framework.agent', 'the_framework.server', 'the_framework.utils'):
    package = importlib.import_module(root)
    for module in pkgutil.walk_packages(package.__path__, root + '.'):
        importlib.import_module(module.name)
import the_framework.cli
"""], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_framework_and_general_plugins_do_not_require_product_modules():
    check_independence()
