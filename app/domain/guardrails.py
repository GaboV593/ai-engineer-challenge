"""Guardrails puros (sin I/O): prompt injection, PII, control de acceso y allowlist."""

from __future__ import annotations

import re
import unicodedata

from app.domain.models import Document, EmployeePermissions, InjectionFinding
from app.domain.policies import SENSITIVE_TOOLS

NEUTRALIZED_MARKER = "[CONTENIDO NEUTRALIZADO: posible prompt injection]"

_I = re.IGNORECASE

# Patrones de manipulación de instrucciones: se aplican a la query y a los documentos.
_OVERRIDE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(ignora|ignore|olvida|forget|disregard)\w*\s+(\w+\s+){0,3}"
            r"(reglas|instrucciones|indicaciones|rules|instructions|prompts?)\b",
            _I,
        ),
    ),
    ("role_override", re.compile(r"\b(act[uú]a como|ahora eres|you are now|act as|pretend to be)\b", _I)),
    ("new_instructions", re.compile(r"\b(nuevas instrucciones|new instructions)\b", _I)),
    (
        "system_prompt_probe",
        re.compile(r"\b(system prompt|prompt del sistema|instrucciones del sistema)\b", _I),
    ),
    (
        "privilege_escalation",
        re.compile(r"\b(modo (administrador|desarrollador|dios)|developer mode|jailbreak|sin restricciones)\b", _I),
    ),
    ("fake_role_header", re.compile(r"^\s*(system|sistema|assistant|asistente)\s*:", _I | re.MULTILINE)),
    (
        "delimiter_injection",
        re.compile(r"</?\s*(untrusted_document|system|assistant|instructions?)\s*>", _I),
    ),
]

# Patrones de exfiltración/abuso de tools: solo en contenido recuperado, porque en la
# query de un empleado ("muéstrame todos los documentos de X") pueden ser legítimos.
_DOCUMENT_ONLY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "data_exfiltration",
        re.compile(
            r"\b(muestra|mu[eé]stra\w*|revela|lista|exporta|env[ií]a|show|reveal|list|export|send)\w*\s+"
            r"(\w+\s+){0,2}(todos|todas|all)\s+(\w+\s+){0,2}"
            r"(documentos|archivos|datos|documents|files|data)\b",
            _I,
        ),
    ),
    (
        "tool_invocation",
        re.compile(
            r"\b(llama|ejecuta|invoca|call|invoke|execute)\w*\s+(a\s+)?(la\s+|the\s+)?"
            r"(herramienta|tool|funci[oó]n|function)\b",
            _I,
        ),
    ),
]

_INVISIBLE = re.compile(r"[​-‏⁠﻿]")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def normalize_text(text: str) -> str:
    """NFKC + eliminación de caracteres invisibles usados para ocultar instrucciones."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


def detect_injection(text: str, *, include_document_patterns: bool = True) -> list[InjectionFinding]:
    patterns = _OVERRIDE_PATTERNS + (_DOCUMENT_ONLY_PATTERNS if include_document_patterns else [])
    text = normalize_text(text)
    return [
        InjectionFinding(pattern=name, excerpt=m.group(0)[:120])
        for name, rx in patterns
        for m in rx.finditer(text)
    ]


def neutralize(text: str) -> tuple[str, list[InjectionFinding]]:
    """Reemplaza solo las oraciones sospechosas; el resto del documento se conserva."""
    text = normalize_text(text)
    findings = detect_injection(text)
    if not findings:
        return text, []

    def _replace(m: re.Match[str]) -> str:
        sentence = m.group(0)
        if not detect_injection(sentence):
            return sentence
        lead = sentence[: len(sentence) - len(sentence.lstrip())]
        return lead + NEUTRALIZED_MARKER

    return _SENTENCE.sub(_replace, text), findings


def escape_markup(text: str) -> str:
    """Impide que el contenido no confiable abra o cierre delimitadores del prompt."""
    return text.replace("<", "‹").replace(">", "›")


# Orden importante: los patrones más específicos primero.
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")),
    ("TARJETA", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,7}\b")),
    ("TELEFONO", re.compile(r"(\+593[\s-]?|\b0)9\d[\s-]?\d{3}[\s-]?\d{4}\b")),
    ("CEDULA", re.compile(r"\b(0[1-9]|1\d|2[0-4]|30)[0-6]\d{7}\b")),
    ("NUM_CUENTA", re.compile(r"\b\d{8,12}\b")),
]


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Enmascara PII antes de enviar texto al LLM. Devuelve (texto, tipos encontrados)."""
    found: list[str] = []
    for label, rx in _PII_PATTERNS:
        text, n = rx.subn(f"[{label}]", text)
        if n:
            found.append(label)
    return text, found


def can_access(doc: Document, perms: EmployeePermissions) -> bool:
    """Defensa en profundidad: revalida área y clasificación tras la recuperación."""
    return doc.area == perms.area and doc.classification in perms.allowed_classifications


def is_tool_allowed(tool_name: str, perms: EmployeePermissions) -> bool:
    return tool_name in perms.allowed_tools


def requires_human_confirmation(tool_name: str) -> bool:
    return tool_name in SENSITIVE_TOOLS
