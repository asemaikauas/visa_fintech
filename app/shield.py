"""Agent Shield: the checks, the decisions, an internal log and the protection pool."""
import base64
import hashlib
import json
import threading
import time
from typing import Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .data import COMPANY, DIRECTORY, POLICY, PURCHASE_ORDERS
from .network import fmt, merchant_name

CHANGE_WORDS = [
    "bank details", "new account", "new visa merchant", "changed its", "pay to our new",
    "updated payment", "new merchant id", "remittance update",
]
POOL_START = 250000.0


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def verify(public_key: bytes, signature_b64: str, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class Shield:
    def __init__(self, bus, network) -> None:
        self.bus = bus
        self.network = network
        self.lock = threading.RLock()
        self.key = Ed25519PrivateKey.generate()      # Agent Shield signs every record entry
        self.cfo_key = Ed25519PrivateKey.generate()  # stands in for the CFO's passkey
        self.agents = {}
        self.reset()

    # ---------- setup ----------
    def reset(self) -> None:
        with self.lock:
            self.payments = {}
            self.idem = {}
            self.record = []
            self.counter = 900
            self.po_remaining = {k: v["amount"] for k, v in PURCHASE_ORDERS.items()}
            self.paid_invoices = set()
            self.known_suppliers = {m for m, d in DIRECTORY.items() if d["acme_known"]}
            self.pool = {"balance": POOL_START, "refunded": 0.0, "recovered": 0.0}
            self.policy = dict(POLICY)
            self.policy_sig = b64(self.cfo_key.sign(canonical(self.policy)))
            self._append("policy_signed", {"company": COMPANY["name"], "version": 1, "signed_by": "CFO (passkey)"})
            for agent in self.agents.values():
                agent["status"] = "active"
                self._append("agent_registered", {"agent": agent["agent_id"], "platform": agent["platform"]})

    def register_agent(self, body: dict) -> dict:
        with self.lock:
            agent = {
                "agent_id": body["agent_id"],
                "name": body.get("name", body["agent_id"]),
                "platform": body.get("platform", "unknown"),
                "public_key": base64.b64decode(body["public_key"]),
                "status": "active",
                "rating": "A",
            }
            self.agents[agent["agent_id"]] = agent
            self._append("agent_registered", {"agent": agent["agent_id"], "platform": agent["platform"]})
            return {"agent_id": agent["agent_id"], "status": "active"}

    def policy_valid(self) -> bool:
        return verify(raw_public_key(self.cfo_key), self.policy_sig, canonical(self.policy))

    # ---------- internal log (not shown on the dashboard) ----------
    def _append(self, kind: str, data: dict) -> dict:
        prev = self.record[-1]["hash"] if self.record else "0" * 64
        entry = {"seq": len(self.record) + 1, "ts": time.strftime("%H:%M:%S"), "kind": kind, "data": data, "prev": prev}
        digest = hashlib.sha256(canonical(entry)).hexdigest()
        entry["hash"] = digest
        entry["shield_sig"] = b64(self.key.sign(digest.encode()))
        self.record.append(entry)
        return entry

    # ---------- payments ----------
    def request_payment(self, raw: bytes, signature: str) -> Tuple[int, dict]:
        try:
            body = json.loads(raw)
        except ValueError:
            return 400, {"status": "rejected", "reason": "invalid_json"}
        agent_id = body.get("agent_id", "")
        with self.lock:
            agent = self.agents.get(agent_id)
            if agent is None:
                return 401, {"status": "rejected", "reason": "unknown_agent", "retry": False}

            if agent["status"] != "active":
                self._append("payment_request", {"agent": agent_id, "invoice": body.get("invoice_ref"), "decision": "blocked", "reason": "agent_revoked"})
                self.bus.emit(
                    "shield", "Blocked: this agent has been switched off",
                    f"{agent_id} was revoked with the company's kill switch. No card, no payment.", "bad",
                    kind="decision", outcome="blocked",
                    checks=[{"s": "bad", "t": "Agent key revoked in Visa's agent directory"}],
                )
                return 403, {"status": "blocked", "reason": "agent_revoked", "retry": False,
                             "message": "This agent has been revoked by the company. Do not retry."}

            if not verify(agent["public_key"], signature, raw):
                self.bus.emit("shield", "Blocked: signature doesn't match", "The request wasn't signed by the registered agent key.", "bad",
                              kind="decision", outcome="blocked", checks=[{"s": "bad", "t": "Signature check failed"}])
                return 401, {"status": "blocked", "reason": "bad_signature", "retry": False}

            key = body.get("idempotency_key", "")
            if key and key in self.idem:
                return self._repeat(key)

            return self._decide(body, raw, signature, agent)

    def _repeat(self, key: str) -> Tuple[int, dict]:
        p = self.payments[self.idem[key]]
        status = "already_paid" if p["status"] == "paid" else p["status"]
        self._append("repeat_request", {"invoice": p["invoice_ref"], "idempotency_key": key, "answer": status})
        self.bus.emit(
            "shield", f"Repeat with key {key}: {status.replace('_', ' ')}",
            f"{p['invoice_ref']} was already handled ({p['id']}). No new card issued, no money moved.",
            "accent", kind="decision", outcome="repeat",
        )
        return 200, {"status": status, "payment_id": p["id"], "retry": False,
                     "message": f"{p['invoice_ref']} was already handled. Do not retry."}

    def _decide(self, body: dict, raw: bytes, signature: str, agent: dict) -> Tuple[int, dict]:
        merchant = body.get("supplier_merchant_id", "")
        po_id = body.get("purchase_order", "")
        invoice_ref = body.get("invoice_ref", "")
        note = body.get("note") or ""
        try:
            amount = float(body.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        checks = [
            {"s": "ok", "t": f"Signed by registered agent {agent['agent_id']} ({agent['platform']})"},
            {"s": "ok" if self.policy_valid() else "bad", "t": "Company Rules v1, signed by the CFO with a passkey"},
        ]
        fraud = False
        po = PURCHASE_ORDERS.get(po_id)
        directory_entry = DIRECTORY.get(merchant)
        if po is None:
            checks.append({"s": "bad", "t": f"Purchase order {po_id or '(none)'} not found"})
        else:
            expected = merchant_name(po["merchant"])
            if merchant == po["merchant"]:
                checks.append({"s": "ok", "t": f"Payee is {expected}'s verified merchant account"})
            else:
                extra = f" ({directory_entry['note']})" if directory_entry and directory_entry.get("note") else ""
                checks.append({"s": "bad", "t": f"Payee {merchant} isn't {expected}'s verified account{extra}"})
                fraud = True
            remaining = self.po_remaining[po_id]
            if remaining <= 0.005:
                checks.append({"s": "bad", "t": f"{po_id} is already fully paid"})
            elif amount <= remaining + 0.005:
                checks.append({"s": "ok", "t": f"{fmt(amount)} fits {po_id} ({fmt(remaining)} still open)"})
            else:
                checks.append({"s": "bad", "t": f"{fmt(amount)} is more than {po_id} allows ({fmt(remaining)} open)"})
        if self.policy["block_payee_changes"] and any(w in note.lower() for w in CHANGE_WORDS):
            checks.append({"s": "bad", "t": "Invoice asks to change bank details, the top sign of invoice fraud"})
            fraud = True
        if (merchant, invoice_ref) in self.paid_invoices:
            checks.append({"s": "bad", "t": f"{invoice_ref} was already paid"})
        hour_ago = time.time() - 3600
        recent = sum(1 for p in self.payments.values() if p["agent"] == agent["agent_id"] and p["created"] > hour_ago)
        limit = self.policy["max_payments_per_hour"]
        checks.append({"s": "ok" if recent < limit else "bad", "t": f"Agent within its speed limit ({recent}/{limit} this hour)"})
        if directory_entry and directory_entry["verified"] and merchant not in self.known_suppliers:
            checks.append({"s": "warn", "t": f"First payment to {directory_entry['name']}"})
        if amount > self.policy["auto_approve_limit"]:
            checks.append({"s": "warn", "t": f"Over the {fmt(self.policy['auto_approve_limit'])} auto-approve limit"})

        if any(c["s"] == "bad" for c in checks):
            outcome = "blocked"
        elif any(c["s"] == "warn" for c in checks):
            outcome = "needs_approval"
        else:
            outcome = "approved"

        self.counter += 1
        pid = f"pay_{self.counter}"
        payment = {
            "id": pid, "agent": agent["agent_id"], "invoice_ref": invoice_ref, "merchant": merchant,
            "merchant_name": merchant_name(merchant), "po": po_id, "amount": amount, "status": outcome,
            "created": time.time(), "key": body.get("idempotency_key", ""), "card_pan": None, "card_last4": None,
            "line_items": body.get("line_items") or [],
        }
        self.payments[pid] = payment
        if payment["key"]:
            self.idem[payment["key"]] = pid
        rec = self._append("payment_request", {
            "payment": pid, "agent": agent["agent_id"], "invoice": invoice_ref, "payee": merchant,
            "amount": amount, "decision": outcome,
            "request_hash": hashlib.sha256(raw).hexdigest()[:16], "agent_sig": signature[:16],
        })
        payment["record_seq"] = rec["seq"]

        title = {
            "approved": "Approved automatically",
            "needs_approval": "Needs a human: sent to the company's CFO",
            "blocked": "Blocked: no card issued, no money moved",
        }[outcome]
        tone = {"approved": "ok", "needs_approval": "warn", "blocked": "bad"}[outcome]
        self.bus.emit("shield", title, f"{pid} · {fmt(amount)} to {payment['merchant_name']} · {invoice_ref}", tone,
                      kind="decision", outcome=outcome, checks=checks)

        if outcome == "approved":
            self._settle(payment)
            return 200, {"status": "approved", "payment_id": pid, "retry": False,
                         "message": f"{COMPANY['bank']} issued a single-use card locked to {payment['merchant_name']} "
                                    f"and Visa AR Manager delivered it to the supplier. Card details are never shared with agents."}
        if outcome == "needs_approval":
            payment["status"] = "pending_approval"
            reasons = "; ".join(c["t"] for c in checks if c["s"] == "warn")
            self.bus.emit("company", f"Approval needed: {fmt(amount)} to {payment['merchant_name']}", reasons, "warn",
                          kind="approval", payment_id=pid)
            return 200, {"status": "needs_approval", "payment_id": pid, "retry": False,
                         "message": "Sent to the company's finance team for one-tap approval."}

        reasons = [c["t"] for c in checks if c["s"] == "bad"]
        if fraud:
            self.bus.emit("company", "Fraud alert sent to the company's CFO",
                          f"{invoice_ref} tried to send {fmt(amount)} to {payment['merchant_name']}. Blocked before any card existed.",
                          "bad")
        return 200, {"status": "blocked", "payment_id": pid, "retry": False, "reasons": reasons,
                     "message": "Blocked by Agent Shield. Do not retry."}

    def _settle(self, payment: dict) -> None:
        card = self.network.issue_single_use(payment["merchant"], payment["amount"], payment["id"])
        self.bus.emit("network", f"Visa AR Manager delivered the card to {payment['merchant_name']}",
                      "It goes straight to the supplier's payment system. The agent never sees the number.", "info")
        self.network.authorize(card["pan"], payment["merchant"], payment["amount"], f"{payment['merchant_name']} billing")
        payment.update(status="paid", card_pan=card["pan"], card_last4=card["last4"])
        self.po_remaining[payment["po"]] -= payment["amount"]
        self.paid_invoices.add((payment["merchant"], payment["invoice_ref"]))
        self.known_suppliers.add(payment["merchant"])
        self._append("payment_settled", {"payment": payment["id"], "card": "••" + card["last4"], "amount": payment["amount"]})

    def get_payment(self, pid: str) -> Optional[dict]:
        with self.lock:
            p = self.payments.get(pid)
            if p is None:
                return None
            return {"payment_id": p["id"], "status": "already_paid" if p["status"] == "paid" else p["status"],
                    "invoice_ref": p["invoice_ref"], "amount": p["amount"], "supplier": p["merchant_name"]}

    def approve(self, pid: str, decision: str) -> Tuple[int, dict]:
        with self.lock:
            p = self.payments.get(pid)
            if p is None or p["status"] != "pending_approval":
                return 409, {"error": "not pending"}
            self._append("human_decision", {"payment": pid, "decision": decision, "by": "CFO (passkey)"})
            if decision == "approve":
                self.bus.emit("company", f"CFO approved {fmt(p['amount'])} to {p['merchant_name']}", "One tap, confirmed with a passkey.", "ok")
                self.bus.emit("shield", "Approved by a human: issuing the card", f"{pid} · {p['invoice_ref']}", "ok",
                              kind="decision", outcome="approved",
                              checks=[{"s": "ok", "t": "Approval signed by the company's CFO (passkey)"}])
                self._settle(p)
            else:
                p["status"] = "denied"
                self.bus.emit("company", f"CFO denied {fmt(p['amount'])} to {p['merchant_name']}", "No card issued.", "bad")
                self.bus.emit("shield", "Denied by a human", f"{pid} · no card issued", "bad", kind="decision", outcome="blocked", checks=[])
            return 200, {"payment_id": pid, "status": p["status"]}

    def pending(self) -> list:
        with self.lock:
            return [{"payment_id": p["id"], "amount": p["amount"], "supplier": p["merchant_name"], "invoice_ref": p["invoice_ref"]}
                    for p in self.payments.values() if p["status"] == "pending_approval"]

    # ---------- disputes and the protection pool ----------
    def dispute(self, body: dict) -> Tuple[int, dict]:
        pid = body.get("payment_id", "")
        line = body.get("line", "")
        amount = float(body.get("amount") or 0)
        with self.lock:
            p = self.payments.get(pid)
            if p is None or p["status"] != "paid":
                return 400, {"error": "payment not found or not paid"}
            po = PURCHASE_ORDERS[p["po"]]
            scene = self.bus.scene
            checks = [
                {"s": "ok", "t": f"The agent's request matched the Company Rules and {p['po']}"},
                {"s": "ok", "t": f"{p['merchant_name']} was paid {fmt(p['amount'])} with single-use card ••{p['card_last4']}"},
                {"s": "bad", "t": f"'{line}' isn't covered by {p['po']} ({po['desc']})"},
            ]
            self.bus.emit("shield", f"Fault found: {p['merchant_name']} billed for something nobody ordered",
                          "The agent followed the rules. The supplier is liable.", "warn",
                          kind="decision", outcome="refund", checks=checks)
            self.pool["balance"] -= amount
            self.pool["refunded"] += amount
            self._append("dispute", {"payment": pid, "line": line, "amount": amount, "liable": "supplier"})
            self.bus.emit("company", f"Refunded {fmt(amount)} to the company from the protection pool",
                          "Instantly. The company doesn't wait for the chargeback.", "ok")
            self.bus.emit("network", f"Chargeback raised against {p['merchant_name']}",
                          "New agent dispute code (proposed)", "accent")

        def recover() -> None:
            time.sleep(3.0)
            with self.lock:
                self.pool["balance"] += amount
                self.pool["recovered"] += amount
                self._append("chargeback_recovered", {"payment": pid, "amount": amount, "from": p["merchant_name"]})
            self.bus.emit("network", f"Chargeback accepted: {p['merchant_name']} returned {fmt(amount)}",
                          "Agent Shield's checks settled it. No back-and-forth.", "ok", scene=scene)
            self.bus.emit("company", f"Protection pool recovered {fmt(amount)}",
                          "The agent platform pays nothing. The agent's rating stays A.", "ok", scene=scene)

        threading.Thread(target=recover, daemon=True).start()
        return 200, {"status": "refunded", "payment_id": pid, "refund": amount, "liable": "supplier"}

    # ---------- kill switch ----------
    def set_agent_status(self, agent_id: str, active: bool) -> Tuple[int, dict]:
        with self.lock:
            agent = self.agents.get(agent_id)
            if agent is None:
                return 404, {"error": "unknown agent"}
            agent["status"] = "active" if active else "revoked"
            self._append("agent_" + ("restored" if active else "revoked"), {"agent": agent_id, "by": "Company (kill switch)"})
        if active:
            self.bus.emit("company", "Agent switched back on", f"{agent_id} can request payments again.", "ok")
        else:
            self.bus.emit("company", "Kill switch pressed", f"{agent_id} is revoked in Visa's agent directory.", "bad")
            self.bus.emit("shield", "Agent key revoked", "All of this agent's payment requests now stop, at every bank, at once.", "bad")
        return 200, {"agent_id": agent_id, "status": agent["status"]}

    # ---------- for the dashboard ----------
    def snapshot(self) -> dict:
        with self.lock:
            agent = next(iter(self.agents.values()), None)
            return {
                "policy": {**self.policy, "signature_valid": self.policy_valid()},
                "agent": None if agent is None else {k: agent[k] for k in ("agent_id", "name", "platform", "status", "rating")},
                "pool": dict(self.pool),
                "approvals": self.pending(),
            }
