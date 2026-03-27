import aiosqlite
from datetime import datetime, timezone

DB_PATH = "sp.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                guild_id  INTEGER NOT NULL,
                sp        INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                title        TEXT NOT NULL,
                description  TEXT NOT NULL,
                sp_reward    INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'open',
                taken_by     INTEGER,
                proof_msg_id INTEGER,
                created_by   INTEGER NOT NULL,
                deadline_hours INTEGER,
                taken_at     TEXT
            )
        """)
        # міграція: додати колонки якщо їх ще немає (для існуючих БД)
        for col, definition in [
            ("deadline_hours", "INTEGER"),
            ("taken_at", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
            except Exception:
                pass
        await db.commit()

# ── users / SP ────────────────────────────────────────────────────────────────

async def get_sp(user_id: int, guild_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT sp FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def set_sp(user_id: int, guild_id: int, sp: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, guild_id, sp) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET sp=excluded.sp
        """, (user_id, guild_id, sp))
        await db.commit()

async def add_sp(user_id: int, guild_id: int, amount: int) -> int:
    current = await get_sp(user_id, guild_id)
    new_sp = current + amount
    await set_sp(user_id, guild_id, new_sp)
    return new_sp

async def get_leaderboard(guild_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, sp FROM users WHERE guild_id=? ORDER BY sp DESC LIMIT ?",
            (guild_id, limit)
        ) as cur:
            return await cur.fetchall()

# ── tasks ─────────────────────────────────────────────────────────────────────

async def create_task(guild_id: int, title: str, description: str,
                      sp_reward: int, created_by: int, deadline_hours: int = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO tasks (guild_id, title, description, sp_reward, created_by, deadline_hours)
               VALUES (?,?,?,?,?,?)""",
            (guild_id, title, description, sp_reward, created_by, deadline_hours)
        )
        await db.commit()
        return cur.lastrowid

async def get_task(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            return await cur.fetchone()

async def get_tasks(guild_id: int, status: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            async with db.execute(
                "SELECT * FROM tasks WHERE guild_id=? AND status=? ORDER BY id DESC",
                (guild_id, status)
            ) as cur:
                return await cur.fetchall()
        async with db.execute(
            "SELECT * FROM tasks WHERE guild_id=? ORDER BY id DESC", (guild_id,)
        ) as cur:
            return await cur.fetchall()

async def take_task(task_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] != 'open':
            return False
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE tasks SET status='taken', taken_by=?, taken_at=? WHERE id=?",
            (user_id, now, task_id)
        )
        await db.commit()
        return True

async def submit_proof(task_id: int, user_id: int, proof_msg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT status, taken_by FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] != 'taken' or row[1] != user_id:
            return False
        await db.execute(
            "UPDATE tasks SET status='pending', proof_msg_id=? WHERE id=?",
            (proof_msg_id, task_id)
        )
        await db.commit()
        return True

async def approve_task(task_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[5] != 'pending':
            return None
        await db.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
        await db.commit()
        return {"id": row[0], "guild_id": row[1], "title": row[2],
                "sp_reward": row[4], "taken_by": row[6]}

async def abandon_task(task_id: int, user_id: int) -> bool:
    """Відмовитись від завдання. Тільки той хто взяв, тільки зі статусу 'taken'."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT status, taken_by FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] != 'taken' or row[1] != user_id:
            return False
        await db.execute(
            "UPDATE tasks SET status='open', taken_by=NULL, taken_at=NULL WHERE id=?",
            (task_id,)
        )
        await db.commit()
        return True

async def reject_task(task_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] != 'pending':
            return False
        await db.execute(
            "UPDATE tasks SET status='open', taken_by=NULL, proof_msg_id=NULL, taken_at=NULL WHERE id=?",
            (task_id,)
        )
        await db.commit()
        return True

async def get_user_stats(user_id: int, guild_id: int) -> dict:
    """Повертає статистику юзера: виконані завдання, загальний заробіток SP."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(sp_reward),0) FROM tasks WHERE taken_by=? AND guild_id=? AND status='done'",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return {"completed": row[0], "earned": row[1]}

async def get_rank(user_id: int, guild_id: int) -> int:
    """Місце юзера в рейтингу."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*)+1 FROM users WHERE guild_id=? AND sp > (SELECT COALESCE(sp,0) FROM users WHERE user_id=? AND guild_id=?)",
            (guild_id, user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 1

async def get_expired_tasks():
    """Повертає завдання з дедлайном що минув (статус taken або pending)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM tasks WHERE status IN ('taken','pending') AND deadline_hours IS NOT NULL AND taken_at IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
    now = datetime.now(timezone.utc)
    expired = []
    for row in rows:
        # row: id,guild_id,title,description,sp_reward,status,taken_by,proof_msg_id,created_by,deadline_hours,taken_at
        taken_at = datetime.fromisoformat(row[10])
        deadline_hours = row[9]
        elapsed = (now - taken_at).total_seconds() / 3600
        if elapsed >= deadline_hours:
            expired.append(row)
    return expired

async def expire_task(task_id: int):
    """Скинути завдання назад в 'open' після дедлайну."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status='open', taken_by=NULL, proof_msg_id=NULL, taken_at=NULL WHERE id=?",
            (task_id,)
        )
        await db.commit()
