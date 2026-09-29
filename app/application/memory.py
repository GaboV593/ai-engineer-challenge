"""Memoria en proceso: historial de conversación y acciones pendientes de confirmación."""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any

from app.domain.models import PendingAction


class InMemoryConversationMemory:
    """Guarda los últimos turnos por empleado (texto ya enmascarado)."""

    def __init__(self, max_turns: int = 3) -> None:
        self._turns: dict[str, deque[tuple[str, str]]] = defaultdict(lambda: deque(maxlen=max_turns))
        self._lock = threading.Lock()

    def history(self, employee_id: str) -> list[dict[str, Any]]:
        with self._lock:
            turns = list(self._turns.get(employee_id, ()))
        messages: list[dict[str, Any]] = []
        for user_text, assistant_text in turns:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        return messages

    def append(self, employee_id: str, user_text: str, assistant_text: str) -> None:
        with self._lock:
            self._turns[employee_id].append((user_text, assistant_text))


class InMemoryPendingActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def save(self, action: PendingAction) -> None:
        with self._lock:
            self._actions[action.confirmation_id] = action

    def get(self, confirmation_id: str) -> PendingAction | None:
        with self._lock:
            return self._actions.get(confirmation_id)

    def delete(self, confirmation_id: str) -> None:
        with self._lock:
            self._actions.pop(confirmation_id, None)
