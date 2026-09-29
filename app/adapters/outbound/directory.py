"""Directorio de empleados en memoria. Implementa EmployeeDirectoryPort."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.domain.models import Employee


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class InMemoryEmployeeDirectory:
    def __init__(self, employees: list[dict[str, Any]], api_keys: dict[str, str]) -> None:
        self._employees = {e["employee_id"]: Employee.model_validate(e) for e in employees}
        # Solo se guardan hashes; la comparación es en tiempo constante.
        self._key_hashes = [(_digest(k), emp_id) for k, emp_id in api_keys.items()]

    def authenticate(self, api_key: str) -> str | None:
        candidate = _digest(api_key)
        match = None
        for key_hash, emp_id in self._key_hashes:
            if hmac.compare_digest(candidate, key_hash):
                match = emp_id
        return match

    def get_employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id)
