import asyncio
from fastapi import WebSocket


class WSManager:
    def __init__(self):
        self._conns: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._conns.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._conns:
            self._conns.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self._conns:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=5)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
