import aiosqlite

DB_PATH = "zouk.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            round_number INTEGER DEFAULT 1
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            game_id TEXT NOT NULL,
            seat_number INTEGER NOT NULL,
            total_score INTEGER DEFAULT 0,
            FOREIGN KEY (game_id) REFERENCES game(id)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            card_count INTEGER NOT NULL,
            trump_suit TEXT NOT NULL,
            FOREIGN KEY (game_id) REFERENCES game(id)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            round_id INTEGER NOT NULL,
            bid INTEGER NOT NULL,
            actual INTEGER,
            score INTEGER,
            FOREIGN KEY (player_id) REFERENCES players(id),
            FOREIGN KEY (round_id) REFERENCES rounds(id)
        );
        """)
        await db.commit()

async def get_round_id(game_id: str, round_number: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM rounds WHERE game_id = ? AND round_number = ?",
            (game_id, round_number)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

async def get_round_scores(round_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT s.player_id, p.name, s.bid, s.actual, s.score
            FROM scores s
            JOIN players p ON s.player_id = p.id
            WHERE s.round_id = ?
            """,
            (round_id,)
        )
        return await cursor.fetchall()

async def get_round_winner(round_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT p.name, s.score
            FROM scores s
            JOIN players p ON s.player_id = p.id
            WHERE s.round_id = ?
            ORDER BY s.score DESC LIMIT 1
            """,
            (round_id,)
        )
        return await cursor.fetchone()

async def get_round_lowest(round_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT p.name, s.score
            FROM scores s
            JOIN players p ON s.player_id = p.id
            WHERE s.round_id = ?
            ORDER BY s.score ASC LIMIT 1
            """,
            (round_id,)
        )
        return await cursor.fetchone()