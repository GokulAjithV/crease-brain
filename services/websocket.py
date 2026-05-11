"""
WebSocket connection manager for live score broadcasting.

Manages per-match connection pools and broadcasts scorecard updates
to all connected viewers of a given match.
"""

from fastapi import WebSocket
from typing import Dict, List


class ConnectionManager:
    """Manages WebSocket connections grouped by match_id."""

    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, match_id: str):
        """Accept a WebSocket connection and add it to the match room."""
        await websocket.accept()
        if match_id not in self.active_connections:
            self.active_connections[match_id] = []
        self.active_connections[match_id].append(websocket)

    def disconnect(self, websocket: WebSocket, match_id: str):
        """Remove a WebSocket from the match room."""
        if match_id in self.active_connections:
            self.active_connections[match_id].remove(websocket)
            if not self.active_connections[match_id]:
                del self.active_connections[match_id]

    async def broadcast(self, match_id: str, message: dict):
        """Send a JSON message to all connections in a match room."""
        if match_id in self.active_connections:
            dead_connections: List[WebSocket] = []
            for connection in self.active_connections[match_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead_connections.append(connection)
            # Clean up dead connections
            for conn in dead_connections:
                self.active_connections[match_id].remove(conn)
            if not self.active_connections[match_id]:
                del self.active_connections[match_id]

    def get_connection_count(self, match_id: str) -> int:
        """Return the number of active viewers for a match."""
        return len(self.active_connections.get(match_id, []))


# Singleton instance — import this across the app
manager = ConnectionManager()
