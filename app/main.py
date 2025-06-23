import os
import base64
import qrcode
import signal
import asyncio
import pathlib
import aiosqlite
from app import db
from io import BytesIO
from typing import List
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

# Scores calculator
def score_round(bid: int, won: int, round_number: int) -> int:
    if bid == 0:
        if won == 0:
            return round_number
        else:
            return -won
    elif bid == won:
        return won * 2
    else:
        return -abs(bid - won)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request, "current_page": "home"})

@app.get("/join", response_class=HTMLResponse)
async def join(request: Request):
    return templates.TemplateResponse("join.html", {"request": request, "current_page": "join"})

@app.post("/join")
async def join_post(name: str = Form(...)):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        cursor = await conn.execute("SELECT id FROM game ORDER BY created_at LIMIT 1")
        game = await cursor.fetchone()
        if not game:
            game_id = "zouk-" + datetime.now().strftime("%H%M%S")
            round_number = 1
            await conn.execute("INSERT INTO game (id,round_number) VALUES (?,?)", (game_id,round_number))
        else:
            game_id = game["id"]

        cursor = await conn.execute("SELECT COUNT(*) FROM players WHERE game_id = ?", (game_id,))
        seat_number = (await cursor.fetchone())[0] + 1

        cursor = await conn.execute(
            "INSERT INTO players (name, game_id, seat_number) VALUES (?, ?, ?)",
            (name, game_id, seat_number)
        )
        await conn.commit()
        player_id = cursor.lastrowid

    return RedirectResponse(url=f"/player/{player_id}", status_code=302)

@app.get("/player/{player_id}", response_class=HTMLResponse)
async def show_player_dashboard(request: Request, player_id: int):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Get player details
        cur = await conn.execute("""
            SELECT name, game_id FROM players WHERE id = ?
        """, (player_id,))
        player = await cur.fetchone()
        if not player:
            return HTMLResponse("Player not found", status_code=404)

        name = player["name"]
        game_id = player["game_id"]

        # Get total score for this player
        cur = await conn.execute("""
            SELECT SUM(points) as total_score FROM scores WHERE player_id = ?
        """, (player_id,))
        total_score = (await cur.fetchone())["total_score"] or 0

        # Get current round number
        cur = await conn.execute("SELECT round_number FROM game WHERE id = ?", (game_id,))
        round_number = (await cur.fetchone())["round_number"]

        # Get rank of this player
        cur = await conn.execute("""
            SELECT p.id, p.name, SUM(s.points) as score FROM players p
            LEFT JOIN scores s ON p.id = s.player_id
            WHERE p.game_id = ?
            GROUP BY p.id
            ORDER BY score DESC
        """, (game_id,))
        leaderboard = await cur.fetchall()
        rank = next((i+1 for i, row in enumerate(leaderboard) if row["id"] == player_id), None)

        # Get latest bid and won (if any)
        cur = await conn.execute("""
            SELECT bid, won FROM scores
            WHERE player_id = ? ORDER BY round_id DESC LIMIT 1
        """, (player_id,))
        latest = await cur.fetchone()
        last_bid = latest["bid"] if latest else None
        last_won = latest["won"] if latest else None

        # Suggest bid (simple example: avoid average, here just mid value)
        player_count = len(leaderboard)
        if round_number <= 2:
            suggested_bid = 1  # too early, low risk
        elif round_number >= 10 and rank == player_count:
            # last player, go bold!
            suggested_bid = round_number
        elif rank == 1:
            # leader plays safe
            suggested_bid = 0
        elif rank >= player_count - 1:
            # trailing or 2nd last
            suggested_bid = round_number - 1
        else:
            # balanced approach
            suggested_bid = round_number // 2


        # Suggestion message
        hint = ""
        if round_number >= 4 and rank >= len(leaderboard) - 1:
            hint = "You're trailing. Consider a bold move! 💥"

    return templates.TemplateResponse("player.html", {
        "request": request,
        "name": name,
        "rank": rank,
        "score": total_score,
        "round_number": round_number,
        "last_bid": last_bid,
        "last_won": last_won,
        "suggested_bid": suggested_bid,
        "hint": hint,
        "current_page": "player"
    })

@app.get("/host", response_class=HTMLResponse)
async def host_panel(request: Request):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id, round_number FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()

        if not game:
            return templates.TemplateResponse("host.html", {
                "request": request,
                "players": [],
                "round_number": None,
                "no_game": True,
                "current_page": "host"
            })

        game_id = game["id"]
        round_number = game["round_number"]

        cur = await conn.execute(
            "SELECT id, name, seat_number FROM players WHERE game_id = ? ORDER BY seat_number ASC", (game_id,)
        )
        players = await cur.fetchall()

    return templates.TemplateResponse("host.html", {
        "request": request,
        "players": players,
        "round_number": round_number,
        "current_page": "host"
    })

@app.post("/host/reorder")
async def reorder_players(request: Request):
    form = await request.form()
    move = form.get("move")

    if not move:
        return RedirectResponse(url="/host", status_code=302)

    direction, player_id = move.split("-")
    player_id = int(player_id)

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, seat_number FROM players WHERE game_id = (SELECT id FROM game ORDER BY created_at DESC LIMIT 1) ORDER BY seat_number ASC"
        )
        players = await cur.fetchall()

        index = next(i for i, p in enumerate(players) if p["id"] == player_id)
        swap_index = index - 1 if direction == "up" else index + 1

        if 0 <= swap_index < len(players):
            p1, p2 = players[index], players[swap_index]
            await conn.execute("UPDATE players SET seat_number = ? WHERE id = ?", (p2["seat_number"], p1["id"]))
            await conn.execute("UPDATE players SET seat_number = ? WHERE id = ?", (p1["seat_number"], p2["id"]))
            await conn.commit()

    return RedirectResponse(url="/host", status_code=302)

@app.post("/player/remove")
async def remove_player(player_id: int = Form(...)):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        await conn.commit()
    return RedirectResponse(url="/host", status_code=302)

@app.post("/game/new")
async def create_new_game():
    game_id = "zouk-" + datetime.now().strftime("%H%M%S")
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("INSERT INTO game (id, round_number, game_started) VALUES (?, ?, ?)", (game_id, 1, False))
        await conn.commit()
    return RedirectResponse(url="/host", status_code=302)

@app.post("/game/start")
async def begin_game():
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Get latest game
        cur = await conn.execute("SELECT id, round_number FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()
        if not game:
            return HTMLResponse("No active game found", status_code=404)

        game_id = game["id"]
        round_count = game["round_number"]

        # Get players ordered by playing order
        cur = await conn.execute("SELECT id, seat_number FROM players WHERE game_id = ? ORDER BY seat_number ASC", (game_id,))
        players = await cur.fetchall()
        if not players:
            return HTMLResponse("No players found for this game", status_code=400)

        starter_index = round_count % len(players)
        starter_id = players[starter_index]["seat_number"]

        # Lock the game and load the leaderboard
        await conn.execute("UPDATE game SET round_number = ?, game_started = 1 WHERE id = ?", (round_count, game_id))

        # Initiate the first round 
        await conn.execute(
            """
            INSERT INTO rounds (game_id, round_number, starter_player_id)
            VALUES (?, ?, ?)
            """,
            (game_id, round_count, starter_id),
        )
        await conn.commit()

    return RedirectResponse(url="/bids", status_code=302)

@app.get("/bids", response_class=HTMLResponse)
async def show_bids(request: Request):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        cur = await conn.execute("SELECT id FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()
        if not game:
            return HTMLResponse("No active game found", status_code=404)
        game_id = game["id"]

        cur = await conn.execute("""
            SELECT id, round_number, round_status FROM rounds 
            WHERE game_id = ? ORDER BY round_number DESC LIMIT 1
        """, (game_id,))
        round_row = await cur.fetchone()
        if not round_row:
            return HTMLResponse("No round found", status_code=404)

        round_id = round_row["id"]
        round_number = round_row["round_number"]
        round_status = round_row["round_status"]

        cur = await conn.execute("""
            SELECT p.id, p.name, p.seat_number, s.bid, s.won 
            FROM players p
            LEFT JOIN scores s ON p.id = s.player_id AND s.round_id = ?
            WHERE p.game_id = ? ORDER BY p.seat_number ASC
        """, (round_id, game_id))
        players = await cur.fetchall()

    return templates.TemplateResponse("bids.html", {
        "request": request,
        "players": players,
        "round_number": round_number,
        "round_status": round_status,
        "round_id": round_id,
        "current_page": "play"
    })

@app.post("/bids")
async def submit_bids_or_wins(request: Request):
    form = await request.form()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        game_cur = await conn.execute("SELECT id FROM game ORDER BY created_at DESC LIMIT 1")
        game = await game_cur.fetchone()
        game_id = game["id"]

        round_cur = await conn.execute(
            "SELECT id, round_number FROM rounds WHERE game_id = ? ORDER BY round_number DESC LIMIT 1",
            (game_id,)
        )
        round_data = await round_cur.fetchone()
        round_id = round_data["id"]
        round_number = round_data["round_number"]

        score_cur = await conn.execute("SELECT COUNT(*) FROM scores WHERE round_id = ?", (round_id,))
        has_bids = (await score_cur.fetchone())[0] > 0

        player_cur = await conn.execute("SELECT id FROM players WHERE game_id = ?", (game_id,))
        players = await player_cur.fetchall()

        if not has_bids:
            for player in players:
                pid = player["id"]
                bid_val = int(form.get(f"bid_{pid}", 0))
                await conn.execute(
                    "INSERT INTO scores (round_id, player_id, bid, won, points) VALUES (?, ?, ?, ?, ?)",
                    (round_id, pid, bid_val, 0, 0)
                )

                # After inserting all bids, mark the round as FINISH
                await conn.execute(
                    "UPDATE rounds SET round_status = 'FINISH' WHERE id = ?",
                    (round_id,)
                )                
        else:
            for player in players:
                pid = player["id"]
                won_val = int(form.get(f"won_{pid}", 0))
                await conn.execute(
                    "UPDATE scores SET won = ? WHERE round_id = ? AND player_id = ?",
                    (won_val, round_id, pid)
                )

                # Fetch bid value
                cur = await conn.execute("SELECT bid FROM scores WHERE round_id = ? AND player_id = ?", (round_id, pid))
                bid = (await cur.fetchone())["bid"]

                # Calculate and update points
                points = score_round(bid, won_val,round_number)
                
                # Update scores with won and calculated points
                await conn.execute(
                    "UPDATE scores SET won = ?, points = ? WHERE round_id = ? AND player_id = ?",
                    (won_val, points, round_id, pid)
                )

            # Increment round number
            new_round_number = round_number + 1
            await conn.execute("UPDATE game SET round_number = ? WHERE id = ?", (new_round_number, game_id))

            # Determine next starter (first player in updated seat order)
            cur = await conn.execute("SELECT id FROM players WHERE game_id = ? ORDER BY seat_number ASC", (game_id,))
            ordered_players = await cur.fetchall()
            new_starter = ordered_players[1]["id"] if len(ordered_players) > 1 else ordered_players[0]["id"]

            await conn.execute(
                "INSERT INTO rounds (game_id, round_number, starter_player_id, round_status) VALUES (?, ?, ?, 'START')",
                (game_id, new_round_number, new_starter)
            )

            # Rotate seat numbers
            total = len(ordered_players)
            for i, player in enumerate(ordered_players):
                new_seat = (i - 1) % total + 1
                await conn.execute("UPDATE players SET seat_number = ? WHERE id = ?", (new_seat, player["id"]))


        await conn.commit()

    return RedirectResponse(url="/bids", status_code=302)

@app.get("/scores", response_class=HTMLResponse)
async def show_scores(request: Request):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Get the latest game
        cur = await conn.execute("SELECT id, round_number, game_started FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()
        if not game:
            return HTMLResponse("No active game found", status_code=404)
        game_id = game["id"]
        game_started = game["game_started"]

        qr_image_base64 = None
        if game_started == 0:
            join_url = str(request.base_url) + "join"
            img = qrcode.make(join_url)
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            qr_image_base64 = base64.b64encode(buffer.getvalue()).decode()

        # Get all players
        cur = await conn.execute("SELECT id, name, seat_number FROM players WHERE game_id = ?", (game_id,))
        players = await cur.fetchall()

        # Get cumulative scores
        scores = []
        for player in players:
            pid = player["id"]
            name = player["name"]
            seat = player["seat_number"]

            score_cur = await conn.execute(
                "SELECT SUM(points) AS total_score FROM scores WHERE player_id = ?", (pid,)
            )
            score_data = await score_cur.fetchone()
            total_score = score_data["total_score"] or 0

            scores.append({
                "id": pid,
                "name": name,
                "seat": seat,
                "total_score": total_score
            })

        # Sort descending by score
        scores.sort(key=lambda x: x["total_score"], reverse=True)

    return templates.TemplateResponse("scores.html", {
        "request": request,
        "scores": scores,
        "game_started": game_started,
        "qr_image_base64": qr_image_base64,
        "current_page": "scores"
    })

@app.post("/reset-db")
async def reset_all():
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("DROP TABLE IF EXISTS scores")
        await conn.execute("DROP TABLE IF EXISTS rounds")
        await conn.execute("DROP TABLE IF EXISTS players")
        await conn.execute("DROP TABLE IF EXISTS game")
        await conn.commit()
    await db.init_db()
    return RedirectResponse(url="/host", status_code=302)

@app.post("/reset-game")
async def reset_keep_players():
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("DELETE FROM scores")
        await conn.execute("DELETE FROM rounds")
        await conn.commit()
        await conn.execute("UPDATE game SET round_number = 1")
        await conn.commit()
    return RedirectResponse(url="/host", status_code=302)

@app.get("/shutdown")
async def shutdown_route():
    print("🔻 Shutdown triggered via route at", datetime.now())
    asyncio.create_task(delayed_shutdown())
    return {"message": "Shutting down..."}

async def delayed_shutdown():
    await asyncio.sleep(1)
    os.kill(os.getpid(), signal.SIGINT)
