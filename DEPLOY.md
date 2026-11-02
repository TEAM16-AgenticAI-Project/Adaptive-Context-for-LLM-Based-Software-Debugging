# Deploy — free, no credit card

The app is a standard Docker web service. Two genuinely free, no-card options. Pick one.
It deploys in offline **sim mode** (no API key, nothing to pay). Switching to the real Groq
model later is optional and also uses a free tier.

---

## Option A — Render (recommended, 3 clicks)

Free plan, no credit card. The service **sleeps after ~15 min idle**; the first request after
sleep takes ~30–60 s to wake, then it's fast. Fine for a demo.

1. Go to <https://render.com> and sign in with GitHub (free).
2. **New ▾ → Blueprint**. Choose the repo **`yashpy/Resumes`** and branch
   **`adaptive-context-ui`**. Render finds `adaptive-context-ui/render.yaml` automatically.
3. Click **Apply / Create**. Render builds the Docker image and gives you a public URL like
   `https://adaptive-context-debugger.onrender.com`. Open it — the UI loads.

That's it. The bundled example works with one click.

### Turn on the real LLM later (optional, still free)
1. Get a free key at <https://groq.com> → API Keys.
2. In Render → your service → **Environment** → add:
   - `DEFAULT_LLM = groq`
   - `GROQ_API_KEY = <your key>`   ← set it here, never commit it
3. Save; Render redeploys. Pick **llm = groq** in the UI.

---

## Option B — Hugging Face Spaces (free, no sleep for light use)

No credit card. Good if you want it always-on.

1. <https://huggingface.co> → sign up (free) → **New → Space**.
2. Name it, pick **Docker** as the SDK, visibility **Public**, **Create**.
3. Upload the contents of this `adaptive-context-ui/` folder to the Space repo
   (drag-drop in the web UI, or `git push` to the Space's git remote). The Space's root must
   contain the `Dockerfile`.
4. The Space builds and serves at `https://<user>-<space>.hf.space`.

HF Spaces expects the app on port `7860` by default; this app honors `$PORT`, and the
Dockerfile exposes `8000`. In the Space **Settings → Variables** add `PORT = 7860` so HF's
router finds it. For the real model, also add `DEFAULT_LLM=groq` and `GROQ_API_KEY` there.

---

## Option C — run it on your own machine (instant, free, private)

```bash
docker compose up --build       # then open http://localhost:8000
```

---

## Notes
- All options run **sim mode** by default: free, no key, no network to any LLM.
- `render.yaml` in this folder pre-configures Render; you don't edit anything.
- Keep `GROQ_API_KEY` only in the platform's env-var settings, never in the repo.
