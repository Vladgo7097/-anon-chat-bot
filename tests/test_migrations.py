import os
import unittest
from pathlib import Path

from sqlalchemy import select, text
from test_chat_duration import DatabaseCase
from models.base import Base
from models.user import User
from migrations.runner import migrate


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "isolated PostgreSQL only")
class MigrationTests(DatabaseCase):
    async def test_legacy_schema_migrates_twice_without_losing_profile(self):
        # DatabaseCase verifies the exact isolated database and hostname first.
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
            await c.execute(text("DROP TABLE IF EXISTS schema_migrations"))
            raw=await c.get_raw_connection()
            await raw.driver_connection.execute(Path("tests/fixtures/legacy-schema.sql").read_text())
            await c.execute(text("""INSERT INTO users
              (telegram_id,anon_id,gender,age,created_at,last_online,chats_count,
               likes_received,dislikes_received,total_chat_seconds,is_premium,
               referrals_count,referrals_activated,streak_days,streak_rewards_claimed,
               positive_rating_pct,is_banned,settings_gender_filter,settings_age_min,
               settings_age_max,total_stars_spent,notification_settings)
              VALUES (123,'#LEGACY','f',24,'2026-08-01 12:00','2026-09-01 12:00',5,
                2,1,300,false,0,0,1,'[]',66.7,false,'a',18,99,99,'{}')"""))
        await migrate()
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await migrate()
        async with self.factory() as s:
            user=await s.scalar(select(User).where(User.telegram_id==123))
            self.assertEqual((user.anon_id,user.gender,user.age,user.chats_count,user.total_chat_seconds,user.total_stars_spent),('#LEGACY','female',24,5,300,99))
            self.assertIsNotNone(user.created_at.tzinfo)
            self.assertEqual(user.created_at.hour,12)
            self.assertFalse(user.onboarding_completed)
            versions=(await s.execute(text("SELECT version FROM schema_migrations"))).all()
            self.assertEqual(len(versions),len(list(Path("migrations").glob("*.sql"))))

    async def test_fresh_schema_migrates_idempotently(self):
        async with self.engine.begin() as c:
            await c.execute(text("DROP TABLE IF EXISTS schema_migrations"))
        await migrate()
        await migrate()
        async with self.factory() as s:
            self.assertIsNotNone(await s.scalar(select(User).where(User.telegram_id==1)))
