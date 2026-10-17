import json

import pytest

from the_framework.server.composition.plugins import PluginStatus


def test_status_is_the_serializable_immutable_payload():
    payload = dict(name="example", installed=True, loaded=True, running=False,
                   binding_available=True, binding_enabled=False,
                   binding_required=False)
    status = PluginStatus(payload)
    assert json.loads(json.dumps(status)) == payload
    assert status.running is False
    with pytest.raises(TypeError):
        status.running = True
    with pytest.raises(TypeError):
        status.update(running=True)
    with pytest.raises((TypeError, ValueError)):
        PluginStatus({**payload, "running": "false"})
