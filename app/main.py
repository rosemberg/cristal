"""Entrypoint Cristal 2.0 — delega para a app factory hexagonal."""

from app.adapters.inbound.fastapi.app import create_app

app = create_app()
