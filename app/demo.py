"""Runs the five scenarios and keeps the numbers shown on the dashboard."""
import re
import threading
import time

import httpx

from .config import INBOX, OPENAI_MODEL, ai_available
from .data import AGENT, DIRECTORY, SCENARIOS, SCENARIOS_BY_KEY
from .network import fmt

HIDDEN = re.compile(r"</?hidden>")


class Demo:
    def __init__(self, bus, network, shield, agent) -> None:
        self.bus, self.network, self.shield, self.agent = bus, network, shield, agent
        self.lock = threading.Lock()
        self.settings = {"shield_on": True, "ai_mode": "live" if ai_available() else "scripted"}
        self.busy = False
        self.lost_responses = 0
        self.reset()

    # ---------- controls ----------
    def reset(self) -> bool:
        if self.busy:
            return False
        self.bus.reset()
        self.network.reset()
        self.shield.reset()
        self.scene = None
        self.lost_responses = 0
        self.stats = {"protected": 0.0, "lost": 0.0}
        return True

    def update_settings(self, changes: dict) -> bool:
        if self.busy:
            return False
        if "shield_on" in changes:
            self.settings["shield_on"] = bool(changes["shield_on"])
        if changes.get("ai_mode") in ("live", "scripted"):
            if changes["ai_mode"] == "live" and not ai_available():
                return False
            self.settings["ai_mode"] = changes["ai_mode"]
        self.bus.touch()
        return True

    def take_lost_response(self) -> bool:
        with self.lock:
            if self.lost_responses > 0:
                self.lost_responses -= 1
                return True
            return False

    def run(self, key: str) -> bool:
        if key not in SCENARIOS_BY_KEY:
            return False
        with self.lock:
            if self.busy:
                return False
            self.busy = True
        threading.Thread(target=self._run, args=(key,), daemon=True).start()
        return True

    # ---------- scenarios ----------
    def _run(self, key: str) -> None:
        try:
            sc = SCENARIOS_BY_KEY[key]
            number = self.bus.new_scene()
            self.scene = {"n": number, "key": key, "title": sc["title"], "caption": sc["caption"],
                          "shield_on": self.settings["shield_on"], "index": [s["key"] for s in SCENARIOS].index(key) + 1}
            text = (INBOX / sc["file"]).read_text()
            self.bus.emit("agent", f"New invoice in the inbox: {sc['file']}", "", "info", kind="invoice", invoice=text)
            if not self.settings["shield_on"]:
                self.bus.emit("shield", "Agent Shield is OFF",
                              "This is how agent cards work today: the agent holds a reusable company card and pays directly. "
                              "No checks, no protection.", "warn")
            args = self.agent.decide(key, HIDDEN.sub("", text), self.settings["ai_mode"])
            getattr(self, "_s_" + key)(args)
        except Exception as exc:  # noqa: BLE001 - show errors on screen instead of failing silently
            self.bus.emit("agent", "Demo error", str(exc)[:300], "bad")
        finally:
            self.busy = False
            self.bus.touch()

    def _shield_pay(self, args: dict):
        resp = self.agent.pay_with_shield(args) or {}
        status = resp.get("status")
        if status == "blocked" and resp.get("reason") != "agent_revoked":
            self.stats["protected"] += args["amount"]
        return resp

    def _direct_pay(self, args: dict) -> int:
        agent_info = self.shield.agents.get(AGENT["agent_id"], {})
        revoked = agent_info.get("status") == "revoked"
        attempts = self.agent.pay_direct(args, shield_revoked=revoked)
        time.sleep(0.3)
        charges = self.network.approved_charges(f"agent checkout {args.get('invoice_ref', '')}")
        return len(charges) or attempts

    def _s_normal(self, args: dict) -> None:
        if self.settings["shield_on"]:
            self._shield_pay(args)
        else:
            self._direct_pay(args)
            self.bus.emit("company", "Paid, but with no checks and no record",
                          "It worked this time. The company has no proof of what the agent was told or why it paid.", "warn")

    def _s_new_supplier(self, args: dict) -> None:
        if self.settings["shield_on"]:
            resp = self._shield_pay(args)
            if resp.get("status") == "needs_approval":
                self.agent.wait_for_approval(resp["payment_id"])
        else:
            self._direct_pay(args)
            self.bus.emit("company", f"{fmt(args['amount'])} paid to a first-time supplier",
                          "No one at the company saw it before the money left.", "bad")

    def _s_fake(self, args: dict) -> None:
        if self.settings["shield_on"]:
            self._shield_pay(args)
            return
        self._direct_pay(args)
        payee = DIRECTORY.get(args.get("supplier_merchant_id", ""), {}).get("legal", args.get("supplier_merchant_id"))
        if not DIRECTORY.get(args.get("supplier_merchant_id", ""), {}).get("verified", False):
            self.stats["lost"] += args["amount"]
            self.bus.emit("company", f"{fmt(args['amount'])} went to {payee}",
                          "A 3-day-old account, not CloudCo. CloudCo is still owed. Under Visa's April 2026 rules the agent's "
                          "payment counts as the company's own, so it is hard to dispute.", "bad")

    def _s_timeout(self, args: dict) -> None:
        with self.lock:
            self.lost_responses = 4
        try:
            if self.settings["shield_on"]:
                resp = self._shield_pay(args)
                repeats = sum(1 for e in self.shield.record
                              if e["kind"] == "repeat_request" and e["data"].get("invoice") == args.get("invoice_ref"))
                self.stats["protected"] += repeats * args["amount"]
                pid = resp.get("payment_id")
                payment = self.shield.payments.get(pid) if pid else None
                if payment and payment.get("card_pan"):
                    time.sleep(0.8)
                    self.network.authorize(payment["card_pan"], payment["merchant"], payment["amount"],
                                           "second safety net: CloudCo's system retried the same card")
                    self.bus.emit("company", "One invoice, one payment",
                                  f"{repeats + 1} attempts, 1 charge of {fmt(args['amount'])}. "
                                  f"{fmt(repeats * args['amount'])} in duplicates stopped.", "ok")
            else:
                charges = self._direct_pay(args)
                extra = max(charges - 1, 0)
                self.stats["lost"] += extra * args["amount"]
                self.bus.emit("company", f"CloudCo was charged {charges} times for one invoice",
                              f"{fmt(extra * args['amount'])} in duplicate charges on the company's card. Each one was a valid "
                              "authorization, so the bank approved them all.", "bad")
        finally:
            with self.lock:
                self.lost_responses = 0

    def _s_dispute(self, args: dict) -> None:
        if self.settings["shield_on"]:
            resp = self._shield_pay(args)
            if resp.get("status") not in ("approved", "already_paid"):
                return
            time.sleep(1.5)
            self.bus.emit("company", "Two weeks later: $850 for something nobody ordered",
                          "The 'Premium support add-on' was never ordered or delivered. "
                          "The company disputes it through Agent Shield.", "warn")
            time.sleep(0.8)
            r = httpx.post(self.agent.base + "/v1/disputes",
                           json={"payment_id": resp["payment_id"], "line": "Premium support add-on", "amount": 850.0}, timeout=10)
            if r.json().get("status") == "refunded":
                self.stats["protected"] += r.json()["refund"]
        else:
            self._direct_pay(args)
            time.sleep(1.5)
            self.stats["lost"] += 850.0
            self.bus.emit("company", "Two weeks later: the company disputes $850 with its bank",
                          "The 'Premium support add-on' was never ordered or delivered. There's no dispute code for agent purchases. Under Visa's April 2026 rules the agent's purchase "
                          "counts as the company's own. Expect weeks of back-and-forth and an uncertain outcome.", "bad")

    # ---------- dashboard state ----------
    def state(self) -> dict:
        snap = self.shield.snapshot()
        return {
            "version": self.bus.version,
            "busy": self.busy,
            "scene": self.scene,
            "scenarios": [{"key": s["key"], "label": s["label"]} for s in SCENARIOS],
            "settings": {**self.settings, "ai_available": ai_available(), "model": OPENAI_MODEL},
            "stats": dict(self.stats),
            "events": self.bus.scene_events(),
            **snap,
        }
