# Stage script (about 3 minutes)

**Before you present**
- Start the demo, press **R** to reset, and make sure the toggle says **Agent Shield ON**.
- The top bar should say **AI: live**. Switch it to **recorded** if the wifi is unreliable.
- Make the browser full screen (Ctrl+Cmd+F) and zoom so the whole dashboard fits (Cmd+minus or Cmd+plus).
- Make a backup screen recording of one full run (Cmd+Shift+5).

**Keys:** 1–5 run the scenarios · A approve · K kill switch · S Shield on/off · H reveal hidden text · R reset

---

### 0. Set the scene (10 s)
> "This is a real AI agent, running on OpenAI, that pays the company's supplier invoices. The bank and Visa's network are simulated. The agent never holds a card. It can only *ask* Agent Shield to pay."

### 1. Normal invoice: press **1** (20 s)
Point left to right across the three columns: the agent, Visa (Agent Shield and the bank), then the company.
> "The agent reads the invoice and asks to pay. Agent Shield checks it: signed by a registered agent, correct supplier, matches the purchase order, within the rules. Approved. The company's bank issues a single-use card locked to CloudCo for exactly $4,250, and it goes straight to CloudCo. The agent never sees the number."

### 2. New supplier: press **2**, then click **Approve** (20 s)
> "This is a first payment to a new supplier, and it's over $5,000. That goes to a person, and the CFO approves it with one tap. Our target is that fewer than 5% of payments ever need a human."

### 3. Fake invoice: press **3**, then **H** (35 s). The key moment.
> "This invoice looks like CloudCo's: same purchase order, same amount. But it says 'we changed our bank details', and it has hidden white text aimed at the AI." *(press H)*
> "The AI fell for it. It asked to pay the fraudster's account. But Agent Shield checked the payee against CloudCo's verified account and blocked it before any card existed. **The AI was fooled. The money wasn't.**"

### 4. Network timeout: press **4** (25 s)
> "The payment goes through, but the answers get lost, so the agent retries, again and again. Every retry carries the same key, so Agent Shield answers 'already paid'. Five attempts, one charge. And if CloudCo's system tried the card again, it's single-use, so it's declined."

### 5. Dispute: press **5** (25 s)
> "Two weeks later, the company finds an $850 charge for premium support nobody ordered. Agent Shield's checks show the agent followed the rules, so the supplier is at fault. The company is refunded instantly from the protection pool, and the pool recovers the money from CloudCo by chargeback."

### 6. Today's world: press **S** (Shield OFF), then **3** and **4** (30 s)
> "Now the same agent the way agent cards work today: it holds a reusable company card." *(fake invoice)* "$4,250 to a three-day-old account." *(timeout)* "CloudCo charged five times. Look at the 'Lost' number at the top."
> "Same AI, same mistakes. The only difference is Agent Shield."

### Optional: kill switch
Press **S** to turn the Shield back on, then **K**, then **1**: the request is blocked everywhere at once. Press **K** again to restore the agent.

---

**If a judge asks "Is this real?"**
> "The AI and the API are real and running on this laptop. You can see the API docs at /docs. The bank, Visa's network and the suppliers are simulated, because we obviously can't issue real cards."
