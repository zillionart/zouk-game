import os
import signal
import asyncio
import pathlib
import aiosqlite
from app import db
from datetime import datetime
from fastapi import FastAPI, Request, Form
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("⚙️ Initializing database...")
    await db.init_db()
    print("✅ Database ready.")
    yield
    print("🛑 FastAPI server is shutting down...")

app = FastAPI(lifespan=lifespan)

BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

templates.env.globals["now"] = datetime.now

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request})

@app.get("/join", response_class=HTMLResponse)
async def join(request: Request):
    return templates.TemplateResponse("join.html", {"request": request})

@app.post("/join")
async def join_post(name: str = Form(...)):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        # Try to fetch the current game
        cursor = await conn.execute("SELECT id FROM game ORDER BY created_at LIMIT 1")
        game = await cursor.fetchone()

        if not game:
            game_id = "zouk-" + datetime.now().strftime("%H%M%S")
            await conn.execute("INSERT INTO game (id) VALUES (?)", (game_id,))
        else:
            game_id = game[0]

        # Insert new player with default seat_number = 0
        cursor = await conn.execute(
            "INSERT INTO players (name, game_id, seat_number) VALUES (?, ?, ?)",
            (name, game_id, 0)
        )
        await conn.commit()
        player_id = cursor.lastrowid

    return RedirectResponse(url=f"/player/{player_id}", status_code=302)

@app.get("/shutdown")
async def shutdown_route():
    print("🔻 Shutdown triggered via route at", datetime.now())
    asyncio.create_task(delayed_shutdown())
    return {"message": "Shutting down..."}

async def delayed_shutdown():
    await asyncio.sleep(1)
    os.kill(os.getpid(), signal.SIGINT)