# Zouk Game

A mobile-first web app companion for the Zouk card game. Uses (HTML + HTMX + FastAPI + SQLite) and is hosted on Digital Ocean.

📦 fastapi: The core web framework
It handles the routes, endpoints (e.g., /join, /leaderboard), and serves HTML or API responses

⚙️ uvicorn: ASGI server to run your FastAPI app
It serves our app with support for async I/O and hot reloading in dev mode
Typical command:
`uvicorn app.main:app --reload`

🧩 jinja2: Is a template rendering engine
FastAPI uses Jinja2 to render HTML templates like join.html, base.html, etc.
HTMX + Tailwind: You’ll use Jinja2 to embed dynamic content into static-looking pages

🗄️ aiosqlite: Async SQLite driver for Python
FastAPI supports async endpoints, and aiosqlite lets you perform DB operations without blocking the server
Used in: db.py or wherever we save player data, bids, scores


Installation:
Run the following command in the project directory
`pip install -r requirements.txt`



Usages: Development Flow
🔄 Dev Workflow Boosters
1. Auto-reload on file change
Run uvicorn like this:
    `uvicorn app.main:app --reload`