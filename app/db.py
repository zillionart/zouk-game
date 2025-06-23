import pathlib
import aiosqlite

DB_PATH = pathlib.Path(__file__).resolve().parent / "zouk.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                round_number INTEGER DEFAULT 0
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                game_id TEXT NOT NULL,
                seat_number INTEGER NOT NULL,
                FOREIGN KEY (game_id) REFERENCES game(id)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                starter_player_id INTEGER,
                round_status TEXT CHECK (round_status IN ('START', 'FINISH')) DEFAULT 'START',
                FOREIGN KEY (game_id) REFERENCES game(id),
                FOREIGN KEY (starter_player_id) REFERENCES players(id)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                bid INTEGER NOT NULL,
                won INTEGER NOT NULL,
                points INTEGER NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id),
                FOREIGN KEY (player_id) REFERENCES players(id)
            );
        """)

        await db.commit()