# Botic

Crash-test platform for B2B payment agents. Reads `scenarios` and `suppliers` from PostgreSQL (Aiven), simulates the agent, writes results to `test_runs`.

## Run locally (Mac)
    pip3 install fastapi psycopg2-binary uvicorn python-dotenv
    cp .env.example .env        # then paste your Aiven Service URI in .env
    python3 -m uvicorn api.index:app --reload
Open http://localhost:8000

## Deploy on Vercel
1. Push this folder to a GitHub repo (.env is ignored).
2. vercel.com > Add New Project > import the repo.
3. Settings > Environment Variables > add DATABASE_URL.
4. Deploy.
