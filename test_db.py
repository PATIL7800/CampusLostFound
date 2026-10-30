
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("DATABASE_URL")

try:
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user;")
            result = cur.fetchone()
            print("Database connection successful!")
            print("Database:", result[0])
            print("User:", result[1])

except Exception as error:
    print("Database connection failed.")
    print("Error:", error)
