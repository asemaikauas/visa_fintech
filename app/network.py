"""A fake issuing bank and card network.

Single-use cards are locked to one merchant and one amount and die after one charge.
The reusable company card (used when Agent Shield is OFF) works until its limit.
"""
import random
import threading
from typing import Optional

from .data import COMPANY, COMPANY_CARD, DIRECTORY


def fmt(amount: float) -> str:
    return "${:,.2f}".format(amount) if round(amount % 1, 2) else "${:,.0f}".format(amount)


def merchant_name(merchant_id: str) -> str:
    return DIRECTORY.get(merchant_id, {}).get("name", merchant_id)


class Network:
    def __init__(self, bus) -> None:
        self.bus = bus
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.cards = {
                COMPANY_CARD["pan"]: {
                    "pan": COMPANY_CARD["pan"],
                    "last4": COMPANY_CARD["pan"][-4:],
                    "type": "reusable",
                    "limit": COMPANY_CARD["limit"],
                    "spent": 0.0,
                }
            }
            self.ledger = []
            self.repeat_cards = {}

    def issue_single_use(self, merchant_id: str, amount: float, payment_id: str,
                         scene: Optional[int] = None, quiet: bool = False) -> dict:
        with self.lock:
            pan = "4000" + "".join(random.choice("0123456789") for _ in range(12))
            card = {
                "pan": pan,
                "last4": pan[-4:],
                "type": "single_use",
                "merchant": merchant_id,
                "amount": amount,
                "used": False,
                "payment_id": payment_id,
            }
            self.cards[pan] = card
        if quiet:
            return card
        self.bus.emit(
            "network",
            f"{COMPANY['bank']} issued single-use card ••{card['last4']}",
            f"Locked to {merchant_name(merchant_id)} for {fmt(amount)}. One charge only.",
            "accent",
            scene=scene,
        )
        return card

    def authorize(self, pan: str, merchant_id: str, amount: float, source: str,
                  scene: Optional[int] = None, quiet: bool = False) -> dict:
        with self.lock:
            card = self.cards.get(pan)
            ok, reason = True, "Approved by the bank"
            if card is None:
                ok, reason = False, "Unknown card"
            elif card["type"] == "single_use":
                if card["used"]:
                    ok, reason = False, "Single-use card already used"
                elif card["merchant"] != merchant_id:
                    ok, reason = False, "Card is locked to a different merchant"
                elif amount > card["amount"] + 0.005:
                    ok, reason = False, "Amount is above the card's lock"
            elif card["spent"] + amount > card["limit"]:
                ok, reason = False, "Over the credit limit"
            if ok and card is not None:
                if card["type"] == "single_use":
                    card["used"] = True
                else:
                    card["spent"] += amount
                    reason = "Approved: valid card, under the limit"
            entry = {
                "id": len(self.ledger) + 1,
                "card": "••" + (card["last4"] if card else pan[-4:]),
                "card_type": card["type"] if card else "unknown",
                "merchant": merchant_name(merchant_id),
                "merchant_id": merchant_id,
                "amount": amount,
                "approved": ok,
                "reason": reason,
                "source": source,
            }
            self.ledger.append(entry)
        if quiet:
            return entry
        verified = DIRECTORY.get(merchant_id, {}).get("verified", False)
        tone = ("ok" if verified else "bad") if ok else "accent"
        title = f"{'APPROVED' if ok else 'DECLINED'}: {fmt(amount)} at {entry['merchant']}"
        detail = f"Card {entry['card']} · {reason} · via {source}"
        group = (pan, merchant_id, source, self.bus.scene)
        if ok and entry["card_type"] == "reusable" and group in self.repeat_cards:
            shown = self.repeat_cards[group]
            shown["count"] += 1
            self.bus.update(shown["event"], tone="bad",
                            title=f"APPROVED ×{shown['count']}: {fmt(amount * shown['count'])} at {entry['merchant']}",
                            detail=f"Card {entry['card']} · every charge was a valid authorization · via {source}")
            return entry
        event = self.bus.emit("network", title, detail, tone, scene=scene)
        if ok and entry["card_type"] == "reusable":
            self.repeat_cards[group] = {"event": event, "count": 1}
        return entry

    def approved_charges(self, source: str) -> list:
        with self.lock:
            return [e for e in self.ledger if e["source"] == source and e["approved"]]
