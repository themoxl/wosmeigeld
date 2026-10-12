import asyncio
import sys
sys.path.append('services/api')
from app.core.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM dishes WHERE id NOT IN (SELECT dish_id FROM dish_ingredients)"))
        print("Deleted empty dishes.")

asyncio.run(main())
