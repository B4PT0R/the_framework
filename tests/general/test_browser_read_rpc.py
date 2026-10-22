"""Element reads retain paging arguments across the private RPC boundary."""
import asyncio

from the_framework.server.clients.browser_rpc import BrowserRPCClient, BrowserRPCServer


def test_read_element_rpc_routes_to_explicit_target(tmp_path):
    async def run():
        class Session:
            async def read_element(self, element_id, **options):
                assert element_id == "fresh-id"
                assert options == {"field": "text", "offset": 4, "limit": 8}
                return {"content": "réponse", "next_offset": None}

        class Service:
            async def stop(self):
                pass

            def session(self, target):
                assert target == "harness_ui"
                return Session()

        path = tmp_path / "browser.sock"
        server = await BrowserRPCServer(path, Service()).start()
        try:
            client = BrowserRPCClient(path)
            result = await asyncio.to_thread(client.session_call, "harness_ui", "read_element",
                                             "fresh-id", field="text", offset=4, limit=8)
            assert result == {"content": "réponse", "next_offset": None}
        finally:
            await server.close()
    asyncio.run(run())
