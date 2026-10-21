"""Check source and wheel imports under the same product-import prohibition."""

import subprocess
import sys


def check_independence():
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
from the_framework.plugins.realtime.controller import RealtimeController
from the_framework.plugins.browser import ChromiumExtension
from the_framework.plugins.bash import BashExtension
from the_framework.plugins.registry import RegistryExtension
from the_framework.plugins.scheduler import SchedulerExtension
from the_framework.plugins.system import SystemExtension
from the_framework.plugins.web_search import WebSearchExtension
from the_framework.plugins.memory.store import MemoryStore
from the_framework.plugins.memory.curator import MemoryCuratorExtension
from the_framework.plugins.memory.agent import MemoryExtension
from the_framework.plugins.realtime import RealtimeExtension, RealtimeConfig
from the_framework.plugins.embeddings import normalize_embedding

store = MemoryStore(':memory:')
assert MappingStore('/tmp/unused-framework-settings', field='settings').field == 'settings'
assert normalize_embedding([3, 4], dimensions=2) == [0.6, 0.8]

for plugin_class in (BashExtension, RegistryExtension, SchedulerExtension,
                     SystemExtension, WebSearchExtension):
    plugin = plugin_class(Agent(client=object()))
    assert plugin.instructions_section() is not None
curator = Agent(client=object()).add_extension(MemoryCuratorExtension)
assert curator.name == 'memory_curator'
class ApplicationMemory(MemoryExtension):
    curator_instructions = 'Preserve supported facts for this application.'
memory = Agent(client=object()).add_extension(ApplicationMemory)
assert memory.instructions_section() is not None
profile = next(value for kind, value in memory._contributions if kind == 'agents' and value.name == 'memory.jiminy')
assert profile.profile().instructions == ApplicationMemory.curator_instructions
browser = ChromiumExtension(Agent(client=object()), runtime_root='/tmp/framework-browser-test')
assert browser.instructions_section() is not None
assert RealtimeConfig().provider_whitelist == []
assert RealtimeConfig(provider_whitelist=['application_state']).provider_whitelist == ['application_state']
voice = Agent(client=object()).add_extension(RealtimeExtension)
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
