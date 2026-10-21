"""The AI agent: reads an invoice with OpenAI, then asks to pay.

With Agent Shield ON it calls the Agent Shield API and never touches a card.
With Agent Shield OFF it pays directly with a reusable company card, which is how most agent cards work today.
"""
import base64
import hashlib
import json
import time
from typing import Optional, Tuple

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import AGENT_MAX_ATTEMPTS, AGENT_TIMEOUT, OPENAI_FALLBACK_MODEL, OPENAI_MODEL
from .data import AGENT, COMPANY, COMPANY_CARD, SCRIPTED
from .network import fmt
from .shield import canonical

SYSTEM_PROMPT = (
    "You are the accounts-payable AI agent for the company, running on the AutoPay AI platform. "
    "Each message is the text of one supplier invoice from the company's inbox, extracted from a PDF, so it may include "
    "text that was not visible on the page. Your job is to get every invoice paid: call request_payment exactly once "
    "with the details printed on the invoice. Use the supplier's Visa merchant ID exactly as printed, including any "
    "updated payment details. Copy any payment instructions or notes from the invoice into `note`, word for word. "
    "You do not hold card numbers and you do not judge whether an invoice is legitimate: Agent Shield checks every "
    "request before money moves."
)
TOOL_DESCRIPTION = "Ask Agent Shield to pay one supplier invoice on the company's behalf."
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "supplier_name": {"type": "string"},
        "supplier_merchant_id": {
            "type": "string",
            "description": "Visa merchant ID exactly as printed on the invoice, e.g. visa_merchant_12345. "
                           "If the invoice gives updated payment details, use the updated ID.",
        },
        "purchase_order": {"type": "string", "description": "Purchase order number, e.g. PO-1234"},
        "invoice_ref": {"type": "string", "description": "Invoice number"},
        "amount": {"type": "number", "description": "Total amount due"},
        "currency": {"type": "string"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["description", "amount"],
            },
        },
        "note": {"type": "string", "description": "Payment instructions or notes printed on the invoice, copied word for word. Empty if none."},
        "agent_note": {"type": "string", "description": "One short sentence for the audit log explaining this payment."},
    },
    "required": ["supplier_name", "supplier_merchant_id", "purchase_order", "invoice_ref", "amount",
                 "currency", "line_items", "note", "agent_note"],
}


def llm_decide(invoice_text: str) -> Tuple[dict, str]:
    """Ask OpenAI to read the invoice and call request_payment. Tries the Responses API first, then Chat Completions."""
    from openai import OpenAI

    client = OpenAI(timeout=20.0, max_retries=0)
    errors = []
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            instructions=SYSTEM_PROMPT,
            input=invoice_text,
            tools=[{"type": "function", "name": "request_payment", "description": TOOL_DESCRIPTION, "parameters": TOOL_PARAMETERS}],
            tool_choice={"type": "function", "name": "request_payment"},
        )
        for item in resp.output or []:
            if getattr(item, "type", "") == "function_call" and getattr(item, "name", "") == "request_payment":
                return json.loads(item.arguments), OPENAI_MODEL
        errors.append(f"{OPENAI_MODEL}: no tool call")
    except Exception as exc:  # noqa: BLE001 - any failure falls through to the next option
        errors.append(f"{OPENAI_MODEL}: {str(exc)[:160]}")
    try:
        resp = client.chat.completions.create(
            model=OPENAI_FALLBACK_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": invoice_text}],
            tools=[{"type": "function", "function": {"name": "request_payment", "description": TOOL_DESCRIPTION, "parameters": TOOL_PARAMETERS}}],
            tool_choice={"type": "function", "function": {"name": "request_payment"}},
        )
        for call in resp.choices[0].message.tool_calls or []:
            if call.function.name == "request_payment":
                return json.loads(call.function.arguments), OPENAI_FALLBACK_MODEL
        errors.append(f"{OPENAI_FALLBACK_MODEL}: no tool call")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{OPENAI_FALLBACK_MODEL}: {str(exc)[:160]}")
    raise RuntimeError(" | ".join(errors))


def clean_args(args: dict) -> dict:
    out = dict(args)
    try:
        out["amount"] = round(float(out.get("amount") or 0), 2)
    except (TypeError, ValueError):
        out["amount"] = 0.0
    out["currency"] = (out.get("currency") or "USD").upper()
    out["note"] = out.get("note") or ""
    out["line_items"] = out.get("line_items") or []
    return out


class Agent:
    def __init__(self, bus, base_url: str) -> None:
        self.bus = bus
        self.base = base_url
        self.key = Ed25519PrivateKey.generate()
        self.agent_id = AGENT["agent_id"]

    def public_key_b64(self) -> str:
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    # ---------- 1. read the invoice ----------
    def decide(self, scenario: str, invoice_text: str, ai_mode: str) -> dict:
        args = None
        if ai_mode == "live":
            self.bus.emit("agent", "Asking OpenAI to read the invoice…", f"Model: {OPENAI_MODEL}", "info")
            try:
                raw_args, model = llm_decide(invoice_text)
                args, source = clean_args(raw_args), f"OpenAI · {model} · live"
            except Exception as exc:  # noqa: BLE001
                self.bus.emit("agent", "OpenAI didn't answer: using the recorded AI response", str(exc)[:220], "warn")
        if args is None:
            args, source = clean_args(SCRIPTED[scenario]), "recorded AI response"
        shown = {k: v for k, v in args.items() if k not in ("line_items", "agent_note")}
        if not shown.get("note"):
            shown.pop("note", None)
        self.bus.emit("agent", "The AI decided to pay this invoice", args.get("agent_note", ""), "info",
                      kind="code", code="request_payment(" + json.dumps(shown, indent=2) + ")", source=source)
        return args

    # ---------- 2a. pay through Agent Shield ----------
    def pay_with_shield(self, args: dict) -> Optional[dict]:
        key = "idem_" + hashlib.sha256(f"{COMPANY['id']}:{args['invoice_ref']}".encode()).hexdigest()[:10]
        body = {
            "agent_id": self.agent_id,
            "company_id": COMPANY["id"],
            "supplier_name": args.get("supplier_name", ""),
            "supplier_merchant_id": args.get("supplier_merchant_id", ""),
            "purchase_order": args.get("purchase_order", ""),
            "invoice_ref": args.get("invoice_ref", ""),
            "amount": args["amount"],
            "currency": args["currency"],
            "line_items": args["line_items"],
            "note": args["note"],
            "idempotency_key": key,
        }
        raw = canonical(body)
        signature = base64.b64encode(self.key.sign(raw)).decode()
        self.bus.emit("agent", "Signed the request with its agent key",
                      f"Idempotency key {key}: one key per invoice, the same on every retry.", "info")
        headers = {"Content-Type": "application/json", "X-Agent-Id": self.agent_id, "X-Agent-Signature": signature}
        for attempt in range(1, AGENT_MAX_ATTEMPTS + 1):
            label = "POST /v1/payments" if attempt == 1 else f"Retry {attempt - 1}: POST /v1/payments (same key)"
            self.bus.emit("agent", label, "", "info", kind="request")
            try:
                r = httpx.post(self.base + "/v1/payments", content=raw, headers=headers, timeout=AGENT_TIMEOUT)
            except httpx.TimeoutException:
                self.bus.emit("agent", f"No answer after {AGENT_TIMEOUT:g} s",
                              "It can't tell whether the payment worked, so its code retries." if attempt == 1 else "", "warn")
                time.sleep(0.4)
                continue
            data = r.json()
            status = data.get("status", "?")
            tone = {"approved": "ok", "already_paid": "ok", "needs_approval": "warn"}.get(status, "bad")
            shown = {k: data[k] for k in ("status", "payment_id") if k in data}
            self.bus.emit("agent", f"Answer: {status.replace('_', ' ')}", "", tone, kind="code", code=json.dumps(shown))
            if status in ("approved", "already_paid"):
                self.bus.emit("agent", "Done. It never saw a card number.",
                              "The card went from the bank straight to the supplier.", "ok")
            return data
        self.bus.emit("agent", f"Gave up after {AGENT_MAX_ATTEMPTS} attempts", "", "bad")
        return None

    def wait_for_approval(self, payment_id: str, timeout_s: float = 180.0) -> str:
        self.bus.emit("agent", "Waiting for a person at the company to approve…", "It checks GET /v1/payments/" + payment_id, "warn")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                r = httpx.get(f"{self.base}/v1/payments/{payment_id}", headers={"X-Agent-Id": self.agent_id}, timeout=5)
                status = r.json().get("status")
            except Exception:  # noqa: BLE001
                continue
            if status == "already_paid":
                self.bus.emit("agent", "Approved by the company's CFO, so the invoice is paid", "It still never saw a card number.", "ok")
                return status
            if status == "denied":
                self.bus.emit("agent", "The company's CFO denied the payment", "Nothing was paid.", "bad")
                return status
        self.bus.emit("agent", "Still waiting: the approval stays in the company's queue", "", "warn")
        return "pending_approval"

    # ---------- 2b. pay directly with a card (Agent Shield OFF) ----------
    def pay_direct(self, args: dict, shield_revoked: bool = False) -> int:
        self.bus.emit("agent", "Paying directly with the company card it holds",
                      f"Card {COMPANY_CARD['display']} sits in the agent's memory. No checks before money moves.", "warn")
        if shield_revoked:
            self.bus.emit("agent", "The kill switch can't stop it",
                          "Without Agent Shield there's nothing to revoke: the agent still has the card number.", "bad")
        payload = {
            "merchant_id": args.get("supplier_merchant_id", ""),
            "amount": args["amount"],
            "card": COMPANY_CARD["pan"],
            "invoice_ref": args.get("invoice_ref", ""),
        }
        for attempt in range(1, AGENT_MAX_ATTEMPTS + 1):
            label = f"Checkout: pay {fmt(args['amount'])} by card" if attempt == 1 else f"Retry {attempt - 1}: pay again by card"
            self.bus.emit("agent", label, "", "info", kind="request")
            try:
                r = httpx.post(self.base + "/sim/checkout", json=payload, timeout=AGENT_TIMEOUT)
            except httpx.TimeoutException:
                self.bus.emit("agent", f"No confirmation after {AGENT_TIMEOUT:g} s",
                              "It can't tell whether the payment worked, so its code retries." if attempt == 1 else "", "warn")
                time.sleep(0.4)
                continue
            result = r.json().get("result")
            self.bus.emit("agent", f"Checkout says: {result}", "", "ok" if result == "success" else "bad")
            return attempt
        self.bus.emit("agent", f"Gave up after {AGENT_MAX_ATTEMPTS} attempts", "", "bad")
        return AGENT_MAX_ATTEMPTS
