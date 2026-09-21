"""The reusable distribution must import without installing the product app."""

import subprocess
import sys


def test_framework_and_general_plugins_do_not_require_product_modules():
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import os
import pkgutil
from pathlib import Path
import sys

class ProductUnavailable(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'the_harness' or fullname.startswith('the_harness.'):
            raise ImportError('product package unavailable: ' + fullname)

sys.meta_path.insert(0, ProductUnavailable())
import the_framework
for module in pkgutil.walk_packages(the_framework.__path__, 'the_framework.'):
    importlib.import_module(module.name)
from the_framework.agent import Agent
from the_framework.utils.persistence import MappingStore
import the_framework.server as server
from the_framework.server.clients.browser import BrowserService
from the_framework.server.clients.browser_rpc import BrowserRPCClient
from the_framework.server.runtime.realtime import RealtimeController
from the_framework.plugins.browser import ChromiumPlugin
from the_framework.plugins.bash import BashPlugin
from the_framework.plugins.registry import RegistryPlugin
from the_framework.plugins.scheduler import SchedulerPlugin
from the_framework.plugins.system import SystemPlugin
from the_framework.plugins.web_search import WebSearchPlugin
from the_framework.plugins.memory.store import MemoryStore
from the_framework.plugins.memory.curator import MemoryCuratorPlugin
from the_framework.plugins.memory.plugin import MemoryPlugin
from the_framework.plugins.realtime import RealtimePlugin, RealtimeConfig
from the_framework.plugins.embeddings import normalize_embedding

store = MemoryStore(':memory:')
assert MappingStore('/tmp/unused-framework-settings', field='settings').field == 'settings'
assert normalize_embedding([3, 4], dimensions=2) == [0.6, 0.8]

for plugin_class in (BashPlugin, RegistryPlugin, SchedulerPlugin,
                     SystemPlugin, WebSearchPlugin):
    plugin = plugin_class(Agent(client=object()))
    assert plugin.instructions_section() is not None
curator = Agent(client=object()).add_plugin(MemoryCuratorPlugin)
assert curator.name == 'memory_curator'
class ApplicationMemory(MemoryPlugin):
    curator_instructions = 'Preserve supported facts for this application.'
memory = Agent(client=object()).add_plugin(ApplicationMemory)
assert memory.instructions_section() is not None
profile = next(value for kind, value in memory._contributions if kind == 'agents')
assert profile.profile().instructions == ApplicationMemory.curator_instructions
browser = ChromiumPlugin(Agent(client=object()), runtime_root='/tmp/framework-browser-test')
assert browser.instructions_section() is not None
assert RealtimeConfig().provider_whitelist == []
assert RealtimeConfig(provider_whitelist=['application_state']).provider_whitelist == ['application_state']
voice = Agent(client=object()).add_plugin(RealtimePlugin)
assert voice.name == 'realtime'
package_root = os.environ.get('FRAMEWORK_PACKAGE_ROOT')
if package_root:
    for name, module in tuple(sys.modules.items()):
        if name.split('.')[0] == 'the_framework':
            assert Path(module.__file__).is_relative_to(package_root), name
"""],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
