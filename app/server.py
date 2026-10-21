"""Agent Shield concept demo: one local server for the API, the fake bank and the dashboard.

Run:  python -m app.server   then open http://localhost:8000
"""
import asyncio

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .agent import Agent
from .config import AGENT_TIMEOUT, OPENAI_MODEL, PORT, STATIC, ai_available
from .data import AGENT
from .demo import Demo
from .events import Bus
from .network import Network

from .shield import Shield

bus = Bus()
network = Network(bus)
shield = Shield(bus, network)
agent = Agent(bus, f"http://127.0.0.1:{PORT}")
shield.register_agent({**AGENT, "public_key": agent.public_key_b64()})
demo = Demo(bus, network, shield, agent)

app = FastAPI(title="Agent Shield (concept demo)", version="0.1")


async def maybe_lose_response() -> None:
    """In the timeout scenario, hold the answer back until the agent has given up waiting."""
    if demo.take_lost_response():
        await asyncio.sleep(AGENT_TIMEOUT + 1.0)


# ---------- Agent Shield API ----------
@app.post("/v1/agents")
async def register_agent(request: Request):
    return await run_in_threadpool(shield.register_agent, await request.json())


@app.post("/v1/agents/{agent_id}/revoke")
async def revoke_agent(agent_id: str):
    status, data = await run_in_threadpool(shield.set_agent_status, agent_id, False)
    return JSONResponse(data, status_code=status)


@app.post("/v1/agents/{agent_id}/restore")
async def restore_agent(agent_id: str):
    status, data = await run_in_threadpool(shield.set_agent_status, agent_id, True)
    return JSONResponse(data, status_code=status)


@app.get("/v1/policies")
async def get_policy():
    return {**shield.policy, "signed_by": "CFO (passkey)", "signature_valid": shield.policy_valid()}


@app.post("/v1/payments")
async def request_payment(request: Request):
    raw = await request.body()
    status, data = await run_in_threadpool(shield.request_payment, raw, request.headers.get("x-agent-signature", ""))
    await maybe_lose_response()
    return JSONResponse(data, status_code=status)


@app.get("/v1/payments/{payment_id}")
async def get_payment(payment_id: str):
    data = shield.get_payment(payment_id)
    return JSONResponse(data or {"error": "not found"}, status_code=200 if data else 404)


@app.post("/v1/payments/{payment_id}/approval")
async def approve_payment(payment_id: str, request: Request):
    body = await request.json()
    decision = "approve" if body.get("decision") == "approve" else "deny"
    status, data = await run_in_threadpool(shield.approve, payment_id, decision)
    return JSONResponse(data, status_code=status)


@app.post("/v1/disputes")
async def open_dispute(request: Request):
    status, data = await run_in_threadpool(shield.dispute, await request.json())
    return JSONResponse(data, status_code=status)


# ---------- a supplier checkout page, used only when Agent Shield is OFF ----------
@app.post("/sim/checkout")
async def checkout(request: Request):
    body = await request.json()
    entry = await run_in_threadpool(
        network.authorize, body.get("card", ""), body.get("merchant_id", ""), float(body.get("amount") or 0),
        f"agent checkout {body.get('invoice_ref', '')}",
    )
    await maybe_lose_response()
    return {"result": "success" if entry["approved"] else "declined", "reason": entry["reason"]}


# ---------- dashboard ----------
@app.get("/demo/state")
async def state():
    return demo.state()


@app.post("/demo/run/{key}")
async def run(key: str):
    return {"started": demo.run(key)}


@app.post("/demo/reset")
async def reset():
    return {"ok": demo.reset()}


@app.post("/demo/settings")
async def settings(request: Request):
    return {"ok": demo.update_settings(await request.json()), "settings": demo.settings}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


def main() -> None:
    mode = f"live OpenAI ({OPENAI_MODEL})" if ai_available() else "recorded AI responses (no OPENAI_API_KEY found)"
    print("\n  Agent Shield concept demo")
    print(f"  Dashboard: http://localhost:{PORT}")
    print(f"  API docs:  http://localhost:{PORT}/docs")
    print(f"  AI mode:   {mode}")
    print("  Stop with Ctrl+C\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
