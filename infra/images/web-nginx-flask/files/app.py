"""A small web application: a page, a health check and an in-memory item list."""

from __future__ import annotations

import threading

from flask import Flask, jsonify, request

app = Flask(__name__)
_items: list[str] = []
_lock = threading.Lock()


@app.get("/")
def index() -> str:
    return "hello from the web application\n"


@app.get("/health")
def health() -> tuple[object, int]:
    return jsonify(status="ok"), 200


@app.get("/items")
def list_items() -> object:
    with _lock:
        return jsonify(items=list(_items))


@app.post("/items")
def add_item() -> tuple[object, int]:
    name = (request.get_json(silent=True) or {}).get("name")
    if not isinstance(name, str) or not name:
        return jsonify(error="name is required"), 400
    with _lock:
        _items.append(name)
        count = len(_items)
    return jsonify(count=count), 201
