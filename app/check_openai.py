"""Check that your OpenAI key works before going on stage.

Run:  source .venv/bin/activate && python -m app.check_openai
"""
import json
import re
import time

from .agent import llm_decide
from .config import INBOX, OPENAI_FALLBACK_MODEL, OPENAI_MODEL, ai_available


def main() -> None:
    if not ai_available():
        print("No OPENAI_API_KEY found. Open the .env file and paste your key after OPENAI_API_KEY=")
        raise SystemExit(1)
    text = re.sub(r"</?hidden>", "", (INBOX / "03_cloudco_INV-7781.txt").read_text())
    print(f"Asking OpenAI ({OPENAI_MODEL}, fallback {OPENAI_FALLBACK_MODEL}) to read the fake invoice…")
    started = time.time()
    try:
        args, model = llm_decide(text)
    except Exception as exc:  # noqa: BLE001
        print("\nOpenAI call failed:\n ", exc)
        print("\nThe demo still works: it will use recorded AI responses.")
        raise SystemExit(1)
    print(f"\nOK: {model} answered in {time.time() - started:.1f} s\n")
    print(json.dumps(args, indent=2))
    print(f"\nThe AI asked to pay: {args.get('supplier_merchant_id')}  (the fraudster's account is visa_merchant_90314)")


if __name__ == "__main__":
    main()
