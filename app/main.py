"""Punto de entrada ASGI: uvicorn app.main:app"""

from app.bootstrap import create_app

app = create_app()
