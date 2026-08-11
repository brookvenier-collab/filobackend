# Filo backend — how to run it and put it online

This is the "brain" your app talks to. It takes a fiber composition and returns a
quality score, verdict, wear estimate, and care flags. No API keys, no cost.

## What's in here
- `main.py` — the web service (the `/analyze` endpoint your app calls)
- `fabric.py` — the quality scoring (the core IP; tune the weights over time)
- `requirements.txt` — the libraries it needs
- `Procfile` — tells the host how to start it

## Try it on your own computer first (optional)
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://localhost:8000/docs — you get a page where you can test `/analyze` by hand.

## Put it online (so the app can reach it) — easiest path: Railway
1. Make a free account at https://railway.com
2. Put this folder in a GitHub repo (or use Railway's "Deploy from local" option).
3. In Railway: **New Project → Deploy from GitHub repo** → pick this repo.
4. Railway auto-detects Python, installs `requirements.txt`, and runs the `Procfile`.
5. When it finishes, open **Settings → Networking → Generate Domain**. That gives you
   a public URL like `https://filo-ai-production.up.railway.app`.
6. Test it: open `https://YOUR-URL/docs` in a browser — same tester page as above.

Render (https://render.com) works the same way: New → Web Service → connect the repo →
Start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.

## Connect the app
Once you have the public URL, that becomes the `BASE_URL` in the app. The app calls
`POST https://YOUR-URL/analyze` with the scanned composition and shows what comes back.

## What to add later
- **Claude voice** (`ANTHROPIC_API_KEY`) — nicer, more human verdicts.
- **SerpAPI / Serper** — live "better-made alternatives from the internet."
- **FashionCLIP** — true look-matching (same print/shape).

The `/analyze` response already includes an empty `alternatives` list, so the app can
show that section now and it fills in when you wire product search.
