import asyncio, os, sys
sys.path.insert(0, "src")
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'graphclaw')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'graphclaw_dev')
from graphclaw.db.age.connection import create_pool

async def main():
    pool = await create_pool('postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw')
    async with pool.connection() as conn:
        result = await conn.execute(
            "SELECT name FROM ag_catalog.ag_label WHERE graph = "
            "(SELECT graphid FROM ag_catalog.ag_graph WHERE name='graphclaw') "
            "ORDER BY name;"
        )
        rows = await result.fetchall()
    print([r[0] for r in rows])
    await pool.close()

asyncio.run(main())
