import json
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from app import cache


class WalkingCacheMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_context_and_map_keep_age_and_other_fields(self):
        original = cache.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            cache.DB_PATH = Path(folder) / "test.sqlite3"
            try:
                await cache.init()
                stamp = "2026-09-16T00:00:00+00:00"
                pack = {"route_center": {"distance_km": 7.4, "drive_min": 12, "walk_min": 12}, "poi": ["retained"]}
                async with aiosqlite.connect(cache.DB_PATH) as db:
                    for bucket in ("context", "map3d"):
                        await db.execute("INSERT INTO kv_cache VALUES(?,?,?,?)", (bucket, "test", json.dumps(pack), stamp))
                    await db.commit()
                await cache.init()
                await cache.init()  # repeat startup must be harmless
                for bucket in ("context", "map3d"):
                    fixed = await cache.kv_get(bucket, "test")
                    self.assertEqual(fixed["route_center"]["walk_min"], 111)
                    self.assertEqual(fixed["route_center"]["drive_min"], 12)
                    self.assertEqual(fixed["poi"], ["retained"])
                    self.assertTrue(fixed["route_center"]["walk_estimated"])
                async with aiosqlite.connect(cache.DB_PATH) as db:
                    async with db.execute("SELECT created_at FROM kv_cache") as cur:
                        self.assertEqual([r[0] for r in await cur.fetchall()], [stamp, stamp])
            finally:
                cache.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
