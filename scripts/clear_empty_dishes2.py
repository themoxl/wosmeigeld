import os
import asyncio
from dotenv import load_dotenv

# Try importing asyncpg, which is what the project actually uses
import asyncpg

load_dotenv('services/api/.env')

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await conn.execute("DELETE FROM dishes WHERE id NOT IN (SELECT dish_id FROM dish_ingredients)")
    print("Deleted empty dishes!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
