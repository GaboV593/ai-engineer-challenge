"""Política RBAC declarativa: el rol define nivel de clasificación y allowlist de tools."""

from __future__ import annotations

from app.domain.models import Classification, Employee, EmployeePermissions, RolePolicy

SEARCH_DOCUMENTS = "search_documents"
GET_EMPLOYEE_PERMISSIONS = "get_employee_permissions"
EXPORT_DOCUMENTS = "export_documents"

ROLE_POLICIES: dict[str, RolePolicy] = {
    "comercial": RolePolicy(
        max_classification=Classification.INTERNO,
        allowed_tools=frozenset({SEARCH_DOCUMENTS}),
    ),
    "analista": RolePolicy(
        max_classification=Classification.CONFIDENCIAL,
        allowed_tools=frozenset({SEARCH_DOCUMENTS, GET_EMPLOYEE_PERMISSIONS}),
    ),
    "gerente": RolePolicy(
        max_classification=Classification.RESTRINGIDO,
        allowed_tools=frozenset({SEARCH_DOCUMENTS, GET_EMPLOYEE_PERMISSIONS, EXPORT_DOCUMENTS}),
    ),
}

# Lista fija: la necesidad de confirmación humana nunca la decide el modelo.
SENSITIVE_TOOLS: frozenset[str] = frozenset({EXPORT_DOCUMENTS})


def resolve_permissions(
    employee: Employee, policies: dict[str, RolePolicy] = ROLE_POLICIES
) -> EmployeePermissions:
    """Calcula los permisos efectivos. Deny by default si el rol no tiene política."""
    policy = policies.get(employee.role)
    return EmployeePermissions(
        employee_id=employee.employee_id,
        role=employee.role,
        area=employee.area,
        max_classification=policy.max_classification if policy else None,
        allowed_tools=policy.allowed_tools if policy else frozenset(),
    )
