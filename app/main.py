import os
import base64
import qrcode
import signal
import random
import asyncio
import pathlib
import aiosqlite
from app import db
from io import BytesIO
from typing import List
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect

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

# Store active connections globally
active_connections: list[WebSocket] = []


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

# Suggested bid logic 
def suggest_bid(round_number: int, rank: int, total_players: int, last_bid: int | None = None) -> int:
    if round_number == 0:
        return 0

    weights = []
    for b in range(round_number + 1):
        score = 10 if b == 0 else 2 * b  # Zouk = 10 pts, others = 2×bid
        if last_bid is not None and b == last_bid:
            score -= 1  # discourage repeat
        if rank > total_players // 2:
            score *= 1.2  # encourage comeback bids
        weights.append(score)

    best_bid = max(range(len(weights)), key=lambda i: weights[i])
    return best_bid


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

@app.get("/rules", response_class=HTMLResponse)
async def zouk_rules(request: Request):
    return templates.TemplateResponse("rules.html", {"request": request})

@app.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    return templates.TemplateResponse("feedback.html", {"request": request})

@app.get("/player/{id}", response_class=HTMLResponse)
async def player_view(request: Request, id: int):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Get player details
        player_cur = await conn.execute("SELECT id, name, game_id FROM players WHERE id = ?", (id,))
        player = await player_cur.fetchone()
        if not player:
            return HTMLResponse("Player not found", status_code=404)

        player_id = player["id"]
        name = player["name"]
        game_id = player["game_id"]

        # Get current round
        round_cur = await conn.execute("SELECT round_number FROM game WHERE id = ?", (game_id,))
        round_data = await round_cur.fetchone()
        round_number = round_data["round_number"] if round_data else 0

        # Get total players
        total_players_cur = await conn.execute("SELECT COUNT(*) as total FROM players WHERE game_id = ?", (game_id,))
        total_players = (await total_players_cur.fetchone())["total"]

        # Score and rank
        score_rows = await conn.execute("""
            SELECT player_id, SUM(points) AS total_score
            FROM scores
            WHERE player_id IN (SELECT id FROM players WHERE game_id = ?)
            GROUP BY player_id
        """, (game_id,))
        score_data = await score_rows.fetchall()
        scores = {row["player_id"]: row["total_score"] or 0 for row in score_data}
        score = scores.get(id, 0)
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        rank = next((i + 1 for i, (pid, _) in enumerate(sorted_scores) if pid == id), total_players)
        is_last = (rank == total_players)

        # Last round bid and win
        last_round_cur = await conn.execute("SELECT id FROM rounds WHERE game_id = ? ORDER BY round_number DESC LIMIT 1", (game_id,))
        last_round = await last_round_cur.fetchone()
        last_bid = last_won = None
        if last_round:
            score_cur = await conn.execute("SELECT bid, won FROM scores WHERE round_id = ? AND player_id = ?", (last_round["id"], id))
            score_row = await score_cur.fetchone()
            if score_row:
                last_bid = score_row["bid"]
                last_won = score_row["won"]

        # Make a recommended bid and drop hints 
        suggested_bid = suggest_bid(round_number, rank, total_players, last_bid)
        top_score = sorted_scores[0][1] if sorted_scores else 0
        gap = top_score - score
        hint = None

        if gap >= 20:
            hint = "⚠️ You’re falling behind. Consider a bold move or a Zouk bid."
        elif gap >= 10:
            hint = "💡 A smart bid can close the gap. Stay sharp!"
        elif suggested_bid == last_bid:
            hint = "Try a different bid this time."

        return templates.TemplateResponse("player.html", {
            "request": request,
            "hide_header": True,
            "player_id": player_id,
            "name": name,
            "round_number": round_number,
            "score": score,
            "rank": rank,
            "total_players": total_players,
            "last_bid": last_bid,
            "last_won": last_won,
            "suggested_bid": suggested_bid,
            "hint": hint,
            "is_last": is_last
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
        # Broadcast scores. Safely update to all connected websockets
        await safe_broadcast_scores_update()
    
    return RedirectResponse(url="/host", status_code=302)

@app.post("/game/new")
async def create_new_game():
    game_id = "zouk-" + datetime.now().strftime("%H%M%S")
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("INSERT INTO game (id, round_number, game_status) VALUES (?, ?, ?)", (game_id, 1, 0))
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
        await conn.execute("UPDATE game SET round_number = ?, game_status = 1 WHERE id = ?", (round_count, game_id))

        # Initiate the first round 
        await conn.execute(
            """
            INSERT INTO rounds (game_id, round_number, starter_player_id)
            VALUES (?, ?, ?)
            """,
            (game_id, round_count, starter_id),
        )
        await conn.commit()

        # Broadcast scores. Safely update to all connected websockets
        await safe_broadcast_scores_update()

    return RedirectResponse(url="/bids", status_code=302)

@app.get("/bids", response_class=HTMLResponse)
async def show_bids(request: Request):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        cur = await conn.execute("SELECT id, game_status FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()
        if not game:
            return HTMLResponse("No active game found", status_code=404)
        
        if game["game_status"] == 2:
            # Redirect to host if game is already closed
            return RedirectResponse(url="/host", status_code=302)

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
        "game_status": game["game_status"],
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

        # Broadcast scores. Safely update to all connected websockets
        await safe_broadcast_scores_update()

    return RedirectResponse(url="/bids", status_code=302)

@app.get("/scores", response_class=HTMLResponse)
async def show_scores(request: Request):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Get the latest game
        cur = await conn.execute("SELECT id, round_number, game_status FROM game ORDER BY created_at DESC LIMIT 1")
        game = await cur.fetchone()
        if not game:
            return HTMLResponse("No active game found", status_code=404)
        game_id = game["id"]
        game_status = game["game_status"]
        round_number = game["round_number"]

        qr_image_base64 = None
        if game_status == 0:
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

            # Get bid for current round
            bid_cur = await conn.execute("""
                SELECT s.bid FROM scores s
                JOIN rounds r ON s.round_id = r.id
                WHERE s.player_id = ? AND r.game_id = ? AND r.round_number = ?
            """, (pid, game_id, round_number))
            bid_row = await bid_cur.fetchone()
            bid = bid_row["bid"] if bid_row else None

            scores.append({
                "id": pid,
                "name": name,
                "seat": seat,
                "bid": bid,
                "total_score": total_score
            })

        # Sort descending by score
        scores.sort(key=lambda x: x["total_score"], reverse=True)

    return templates.TemplateResponse("scores.html", {
        "request": request,
        "scores": scores,
        "game_status": game_status,
        "round_number": round_number,
        "qr_image_base64": qr_image_base64,
        "current_page": "scores"
    })

@app.websocket("/socket/scores")
async def scores_websocket(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    print(f"📡 WebSocket connected — total: {len(active_connections)}")
    try:
        while True:
            await websocket.receive_text()  # Keep connection open
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print(f"🔌 WebSocket disconnected — total: {len(active_connections)}")

async def broadcast_scores_update():
    for connection in active_connections:
        try:
            await connection.send_text("update")
        except Exception:
            active_connections.remove(connection)  

# Prevent accidental cascade during page reloads or solo play
async def safe_broadcast_scores_update(force=False):
    print(f"🧩 WebSocket clients connected: {len(active_connections)}")
    if force or len(active_connections) > 1:
        print("📣 Broadcasting score update")
        await broadcast_scores_update()
    else:
        print("🔇 Suppressed broadcast — not enough active clients")

# Player poll for keeping track of round status     
@app.get("/player/{id}/checkin")
async def round_check(id: int):
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT g.round_number, p.name
            FROM players p JOIN game g ON p.game_id = g.id
            WHERE p.id = ?
        """, (id,))
        row = await cur.fetchone()
        if row:
            print("Player check-in for:", row["name"])
            return {"round_number": row["round_number"]}
        else:
            print("❌ Player not found for ID:", id)
            return {"round_number": 0}

@app.post("/game/close")
async def close_game():
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("UPDATE game SET game_status = 2")
        await conn.commit()
    
    # Broadcast scores. Safely update to all connected websockets
    await safe_broadcast_scores_update()

    return RedirectResponse(url="/host", status_code=302)


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
