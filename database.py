import asyncpg
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=5, max_size=20)
        await self._create_tables()
        logger.info("✅ Database connected")

    async def close(self):
        if self.pool:
            await self.pool.close()

    # =====================================================
    # TABLES
    # =====================================================

    async def _create_tables(self):
        async with self.pool.acquire() as conn:

            # USERS (расширение старой логики)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                tariff_code TEXT DEFAULT 'MINI',
                subscribed_until TIMESTAMP,
                is_frozen BOOLEAN DEFAULT FALSE,
                frozen_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """)

            # ТАРИФЫ
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channels_limit INT,
                posts_per_day INT,
                stars_price INT,
                crypto_price NUMERIC,
                is_active BOOLEAN DEFAULT TRUE
            )
            """)

            # КАНАЛЫ ТАРИФОВ (ПРИВАТНЫЕ)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS tariff_channels (
                tariff_code TEXT REFERENCES tariffs(code) ON DELETE CASCADE,
                channel_id BIGINT UNIQUE NOT NULL,
                invite_link TEXT,
                PRIMARY KEY (tariff_code)
            )
            """)

            # ПОДПИСКИ
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id) ON DELETE CASCADE,
                tariff_code TEXT REFERENCES tariffs(code),
                started_at TIMESTAMP DEFAULT NOW(),
                ends_at TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
            """)

            # КАНАЛЫ ПОЛЬЗОВАТЕЛЯ
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_channels (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id) ON DELETE CASCADE,
                channel_id BIGINT,
                title TEXT,
                active BOOLEAN DEFAULT TRUE
            )
            """)

            # ПОСТЫ (совместимо со старым)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id),
                channel_id BIGINT,
                text TEXT,
                media JSONB,
                scheduled_at TIMESTAMP,
                published BOOLEAN DEFAULT FALSE,
                is_frozen BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """)

            # КРИПТО ОПЛАТА
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_payments (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id),
                tariff_code TEXT,
                amount NUMERIC,
                check_id TEXT,
                confirmed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """)

            await self._seed_tariffs(conn)

    # =====================================================
    # DEFAULT DATA
    # =====================================================

    async def _seed_tariffs(self, conn):
        tariffs = [
            ('MINI', 'MINI', 1, 3, 0, None),
            ('STANDARD', 'STANDARD', 2, 8, 300, None),
            ('PRO', 'PRO', 3, 12, 500, 4),
            ('VIP', 'VIP', 8, 32, 800, 6.5),
        ]

        for t in tariffs:
            await conn.execute("""
            INSERT INTO tariffs (code, name, channels_limit, posts_per_day, stars_price, crypto_price)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (code) DO NOTHING
            """, *t)

    # =====================================================
    # USERS
    # =====================================================

    async def get_or_create_user(self, tg_id, username, full_name):
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id=$1", tg_id
            )
            if user:
                return dict(user)

            user = await conn.fetchrow("""
            INSERT INTO users (telegram_id, username, full_name)
            VALUES ($1,$2,$3)
            RETURNING *
            """, tg_id, username, full_name)

            return dict(user)

    async def set_tariff(self, user_id: int, tariff: str, days=30):
        async with self.pool.acquire() as conn:
            until = datetime.utcnow() + timedelta(days=days)

            await conn.execute("""
            UPDATE users SET tariff_code=$1, subscribed_until=$2, is_frozen=FALSE
            WHERE id=$3
            """, tariff, until, user_id)

            await conn.execute("""
            INSERT INTO subscriptions (user_id, tariff_code, ends_at)
            VALUES ($1,$2,$3)
            """, user_id, tariff, until)

    async def freeze_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            until = datetime.utcnow() + timedelta(days=7)
            await conn.execute("""
            UPDATE users SET is_frozen=TRUE, frozen_until=$1 WHERE id=$2
            """, until, user_id)
