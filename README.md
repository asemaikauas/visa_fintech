# Agent Shield: concept demo

A working local demo of Agent Shield. A real AI agent (OpenAI) reads supplier invoices and asks to pay them. Agent Shield checks every request before any money moves. The bank, the card network and the suppliers are simulated on your laptop.

Everything is fictional test data. This is a student concept, not a Visa product.

## Setup on a Mac (about 5 minutes, once)

1. **Unzip** the folder to your Desktop.
2. **Open Terminal** (Cmd+Space, type "Terminal", press Enter) and go to the folder:
   ```
   cd ~/Desktop/agent-shield-demo
   ```
3. **Add your OpenAI key.** Make a settings file and open it:
   ```
   cp .env.example .env
   open -e .env
   ```
   Paste your key after `OPENAI_API_KEY=` (no quotes, no spaces), then save and close the file.
4. **Start the demo:**
   ```
   bash start.sh
   ```
   The first run installs what it needs, which takes about a minute and needs internet. Your browser then opens **http://localhost:8000**.

To stop the demo, click in Terminal and press **Ctrl+C**. To start it again, run `bash start.sh`. Later starts take about 2 seconds.

If you get "Python 3 isn't installed", install it from python.org, or accept the macOS prompt to install the developer tools, then run `bash start.sh` again.

## Check your OpenAI key before the presentation

With the demo stopped, run:
```
source .venv/bin/activate
python -m app.check_openai
```
You should see "OK" and the AI asking to pay `visa_merchant_90314`, which is the fraudster's account.

The demo tries `OPENAI_MODEL` (default `gpt-6-luna`) first, then `OPENAI_FALLBACK_MODEL` (default `gpt-4o-mini`). You can change either one in `.env`.

## If something goes wrong on stage

| Problem | Fix |
|---|---|
| No internet, or OpenAI is slow or failing | Click **AI: live** in the top bar so it switches to **AI: recorded**. Everything else works offline. |
| OpenAI fails in the middle of a scenario | Nothing to do. The agent says so in amber and uses the recorded answer. |
| The top bar says "AI: recorded (no key)" | The key isn't in `.env`. Fix the file, then restart the demo. |
| "Address already in use" | Another copy is running. Close the other Terminal window, or run `PORT=8001 bash start.sh`. |
| The screen looks messy after rehearsing | Press **R** to reset. |

## What's inside

| Part | File | What it does |
|---|---|---|
| AI agent | `app/agent.py` | OpenAI function calling with one tool, `request_payment`. Signs every request (Ed25519) and adds an idempotency key. It never receives a card number. |
| Agent Shield API | `app/shield.py`, `app/server.py` | `POST /v1/agents`, `POST /v1/payments`, `GET /v1/payments/{id}`, `POST /v1/payments/{id}/approval`, `POST /v1/disputes`, `POST /v1/agents/{id}/revoke`. Checks the payee, purchase order, bank-detail changes, duplicates, speed limit and approval rules. Keeps a protection pool. |
| Bank and network | `app/network.py` | Issues single-use cards locked to one supplier and amount. Approves or declines charges. |
| Dashboard | `app/static/index.html` | The live screen at localhost:8000. |
| Invoices | `inbox/` | The five invoices the agent reads. Number 3 contains hidden text aimed at the AI. |

The interactive API documentation is at **http://localhost:8000/docs**, which is useful if a judge asks whether the API is real. That page needs internet to load; everything else works offline.
