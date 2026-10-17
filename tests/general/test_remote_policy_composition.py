"""Startup composition of installed remote-client feature contracts."""

import asyncio

import pytest

from the_framework.server.clients.remote import ClientRemoteService, RemoteClientPolicy


def test_remote_policy_composes_feature_contributions_without_overwriting_scopes():
    base = RemoteClientPolicy(
        default_scopes=("chat",),
        platform_scopes={"desktop": ("desktop", "full_ui")},
        capabilities=frozenset({"conversation"}),
    )
    media = RemoteClientPolicy(
        default_scopes=("media",),
        platform_scopes={"desktop": ("full_ui", "media")},
        capabilities=frozenset({"media_runtime"}),
        exclusive_capabilities=frozenset({"media_runtime"}),
        unleased_events=frozenset({"media_state"}),
    )
    haptics = RemoteClientPolicy(
        default_scopes=("haptics",),
        capabilities=frozenset({"haptics"}),
        exclusive_capabilities=frozenset({"haptics"}),
        routed_capabilities=frozenset({"haptics"}),
        default_lease_capabilities=frozenset({"haptics"}),
        event_capabilities={"haptic_trace": frozenset({"haptics"})},
    )

    policy = RemoteClientPolicy.compose(base, media, haptics)

    assert policy.scopes_for("desktop") == (
        "chat", "media", "haptics", "desktop", "full_ui",
    )
    assert policy.capabilities == frozenset({
        "conversation", "media_runtime", "haptics",
    })
    assert policy.default_lease_capabilities == frozenset({"haptics"})
    assert policy.event_capabilities == {
        "haptic_trace": frozenset({"haptics"}),
    }
    assert base.capabilities == frozenset({"conversation"})


@pytest.mark.parametrize("field", ["capability", "event"])
def test_remote_policy_rejects_duplicate_feature_ownership(field):
    first = RemoteClientPolicy(
        capabilities=frozenset({"haptics"}),
        exclusive_capabilities=frozenset({"haptics"}),
        event_capabilities={"trace": frozenset({"haptics"})},
    )
    second = (
        RemoteClientPolicy(capabilities=frozenset({"haptics"}))
        if field == "capability"
        else RemoteClientPolicy(unleased_events=frozenset({"trace"}))
    )
    with pytest.raises(ValueError, match="duplicate remote"):
        RemoteClientPolicy.compose(first, second)


def test_remote_service_accepts_policy_only_before_startup(tmp_path):
    class Harness:
        async def stream(self):
            while True:
                await asyncio.sleep(60)
                yield None

    async def run():
        service = ClientRemoteService(
            Harness(), tmp_path / "clients.json",
            policy=RemoteClientPolicy(
                default_scopes=("chat",),
                capabilities=frozenset({"conversation"}),
            ),
        )
        media = RemoteClientPolicy(
            default_scopes=("media",),
            capabilities=frozenset({"media_runtime"}),
        )
        service.add_policy(media)
        assert service.clients.policy is service.policy
        assert service.clients.issue("Phone")[0]["scopes"] == ["chat", "media"]
        await service.start()
        try:
            with pytest.raises(RuntimeError, match="fixed after startup"):
                service.add_policy(RemoteClientPolicy())
        finally:
            await service.stop()

    asyncio.run(run())
