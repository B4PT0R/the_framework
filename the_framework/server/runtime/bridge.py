import asyncio
import inspect
import logging

from starlette.websockets import WebSocketDisconnect

from ...agent.runtime.application import ApplicationError
from ...agent.runtime.protocol import (
    ApplicationCall,
    ApplicationCancel,
    ApplicationRequest,
    ApplicationResult,
)
from the_framework.utils.ids import timestamp_id

logger = logging.getLogger(__name__)


class ApplicationBridge:
    """Route typed application capabilities between a worker and one runtime."""

    def __init__(self, supervisor, *, subprotocol="agent-application"):
        self.supervisor = supervisor
        self.subprotocol = subprotocol
        self.websocket = None
        self.outgoing = asyncio.Queue(128)
        self.pending = {}
        self.local_pending = {}
        self.handlers = {}
        self.event_handlers = {}
        self.events = asyncio.Queue(128)
        self.event_processor_task = None
        self.routers = []

    async def start(self):
        if self.event_processor_task is None:
            self.event_processor_task = asyncio.create_task(
                self.process_events(),
                name="application-event-processor",
            )

    async def stop(self):
        task = self.event_processor_task
        self.event_processor_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        while not self.events.empty():
            self.events.get_nowait()

    def register(self, capability, handler, *, contextual=False):
        if capability in self.handlers:
            raise ValueError(f"duplicate application capability: {capability}")
        self.handlers[capability] = (handler, contextual)

    def register_event(self, name, handler):
        if name in self.event_handlers:
            raise ValueError(f"duplicate application event: {name}")
        self.event_handlers[name] = handler

    def register_router(self, router):
        self.routers.append(router)

    @property
    def connected(self):
        return self.websocket is not None

    async def dispatch(self, output):
        if isinstance(output, ApplicationCancel):
            self.pending.pop(output.call_id, None)
            for router in self.routers:
                if await router.route(output):
                    return
            if not self.connected:
                return
        elif isinstance(output, ApplicationCall):
            registration = self.handlers.get(output.request.capability)
            if registration is not None:
                handler, contextual = registration
                try:
                    kwargs = {"command_id": output.command_id} if contextual else {}
                    result = handler(
                        output.request.method,
                        output.request.payload,
                        **kwargs,
                    )
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as error:
                    await self.fail(output.request.id, str(error))
                else:
                    await self.supervisor.send(ApplicationResult(
                        id=output.request.id,
                        status="success",
                        result=result,
                    ))
                return
            local_call = output.request.id in self.local_pending
            if not local_call:
                self.pending[output.request.id] = output
            for router in self.routers:
                if await router.route(output):
                    return
            if not self.connected:
                self.pending.pop(output.request.id, None)
                if local_call:
                    await self.reject(
                        output.request.id,
                        "application runtime is not connected",
                    )
                else:
                    await self.fail(
                        output.request.id,
                        "application runtime is not connected",
                    )
                return
        try:
            self.outgoing.put_nowait(output)
        except asyncio.QueueFull:
            if isinstance(output, ApplicationCall):
                self.pending.pop(output.request.id, None)
                if output.request.id in self.local_pending:
                    await self.reject(
                        output.request.id,
                        "application runtime bridge is saturated",
                    )
                else:
                    await self.fail(
                        output.request.id,
                        "application runtime bridge is saturated",
                    )

    async def call(self, capability, method, payload=None, *, timeout_ms=30_000):
        registration = self.handlers.get(capability)
        if registration is not None:
            handler, contextual = registration
            kwargs = {"command_id": None} if contextual else {}
            result = handler(method, payload or {}, **kwargs)
            return await result if inspect.isawaitable(result) else result
        request = ApplicationRequest(
            id=timestamp_id(),
            capability=capability,
            method=method,
            payload=payload or {},
            timeout_ms=timeout_ms,
        )
        future = asyncio.get_running_loop().create_future()
        self.local_pending[request.id] = future
        await self.dispatch(ApplicationCall(
            request_id=request.id,
            request=request,
        ))
        try:
            return await asyncio.wait_for(future, timeout_ms / 1000)
        except TimeoutError as error:
            raise ApplicationError(
                f"application capability timed out: {capability}.{method}"
            ) from error
        finally:
            self.local_pending.pop(request.id, None)

    async def fail(self, call_id, error):
        await self.supervisor.send(ApplicationResult(
            id=call_id,
            status="failure",
            error=error,
        ))

    async def reject(self, call_id, error):
        local = self.local_pending.get(call_id)
        if local is not None and not local.done():
            local.set_exception(ApplicationError(error))
            return
        if self.pending.pop(call_id, None) is not None:
            await self.fail(call_id, error)

    async def accept(self, payload, *, responses=None):
        if payload.get("type") == "heartbeat":
            return {"type": "heartbeat_ack"}
        if payload.get("type") == "application_event":
            name = str(payload.get("name") or "")
            event_id = str(payload.get("id") or "")
            handler = self.event_handlers.get(name)
            if handler is None or not event_id:
                raise ValueError(f"unknown or invalid application event: {name}")
            try:
                self.events.put_nowait((
                    event_id,
                    name,
                    handler,
                    payload.get("payload"),
                    responses,
                ))
            except asyncio.QueueFull:
                return {
                    "type": "application_event_ack",
                    "id": event_id,
                    "status": "failure",
                    "error": "application event queue is saturated",
                }
            return None
        if payload.get("type") != "application_result":
            raise ValueError(
                "application bridge accepts application results and events only"
            )
        result = ApplicationResult(**payload)
        local = self.local_pending.get(result.id)
        if local is not None:
            if result.status == "success":
                local.set_result(result.result)
            else:
                local.set_exception(ApplicationError(
                    result.error or "application call failed"
                ))
            return None
        if self.pending.pop(result.id, None) is None:
            raise ValueError(f"unknown or completed application call: {result.id}")
        await self.supervisor.send(result)
        return None

    async def receive(self, websocket, outgoing):
        try:
            while True:
                payload = await websocket.receive_json()
                response = await self.accept(payload, responses=outgoing)
                if response is not None:
                    await outgoing.put(response)
        except asyncio.CancelledError:
            return

    async def process_events(self):
        """Commit runtime telemetry without blocking the control socket.

        Application events are intentionally serialized: several of them
        mutate canonical plugin state, but a slow worker acknowledgement must
        never starve heartbeats or tear down physical device control.
        """
        while True:
            event_id, name, handler, payload, responses = await self.events.get()
            acknowledgement = {
                "type": "application_event_ack",
                "id": event_id,
            }
            try:
                result = handler(event_id, payload)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "application event %s (%s) failed: %s",
                    event_id,
                    name,
                    error,
                )
                acknowledgement.update(status="failure", error=str(error))
            if responses is not None:
                await responses.put(acknowledgement)

    async def send(self, websocket, outgoing):
        try:
            while True:
                await websocket.send_json(await outgoing.get())
        except asyncio.CancelledError:
            return

    async def serve(self, websocket, *, subprotocol=None):
        if self.connected:
            await websocket.close(code=1013)
            return
        self.websocket = websocket
        outgoing = asyncio.Queue(128)
        self.outgoing = outgoing
        await websocket.accept(subprotocol=subprotocol or self.subprotocol)
        sender = asyncio.create_task(self.send(websocket, outgoing))
        receiver = asyncio.create_task(self.receive(websocket, outgoing))
        tasks = (sender, receiver)
        try:
            try:
                done, _pending = await asyncio.wait(
                    (sender, receiver),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                done = set()
            for task in done:
                error = task.exception()
                if error and not isinstance(error, WebSocketDisconnect):
                    raise error
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.websocket = None
            if self.outgoing is outgoing:
                self.outgoing = asyncio.Queue(128)
            calls = tuple(self.pending)
            self.pending.clear()
            local = tuple(self.local_pending.values())
            self.local_pending.clear()
            for call_id in calls:
                await self.fail(call_id, "application runtime disconnected")
            for future in local:
                if not future.done():
                    future.set_exception(ApplicationError(
                        "application runtime disconnected"
                    ))
