# Upwork Proposal Strategist

A simple desktop app that helps you write better Upwork proposals, faster.

It looks at an Upwork job, compares it to your own work files, tells you whether it's worth applying, and writes a first draft of your proposal — using only true facts from your files.

Everything runs on **your own computer**. Your files stay with you.

---

## How to use it

> ## ▶ Double-click **`Start UpworkProposalStrategist`**

That's the only thing you ever need to do. The **first** time, it sets itself up automatically (one time, a few minutes — you'll see a progress window). After that it opens in a few seconds.

**No terminal. No Python. No conda. No setup commands. Ever.**

> 💡 New here? The step-by-step picture guide is **[docs/FIRST_TIME_SETUP_GUIDE.md](docs/FIRST_TIME_SETUP_GUIDE.md)**. Done it before? **[docs/QUICK_START_GUIDE.md](docs/QUICK_START_GUIDE.md)**.

---

## What it does

The app walks you through **five simple pages**, top to bottom on the left:

1. **Setup** — connect your AI service (one-time; it's remembered).
2. **Dossier** — point the app at a folder of your work files.
3. **Job Screenshot** — add a picture of the Upwork job (upload or paste).
4. **Analysis** — see the verdict: 🟢 Apply Confidently · 🟡 Proceed With Caution · 🔴 Do Not Proceed, plus your strengths and concerns.
5. **Proposal** — get a ready-to-copy proposal draft.

Each page unlocks after you finish the one before it (a 🔒 lock icon is normal).

---

## Which AI service can I use?

You only need **one**. You'll need a key (a secret password) from that service — the app's **Setup** page walks you through it.

- **OpenAI** — popular and easy. A good first choice. Can read pictures.
- **Anthropic** — makes the "Claude" AI. Can read pictures.
- **Gemini** — Google's AI. Can read pictures.
- **Groq** — fast and cheap, but **cannot read pictures**.

> ⚠️ The **Job Screenshot** step needs an AI that can read pictures. Use **OpenAI**, **Anthropic**, or **Gemini**. **Groq cannot read pictures.**

---

## Your privacy

- Your original files **never leave your computer**. Only a small, relevant bit of text is sent to the AI service.
- Your secret key is saved in a private file on your computer (in `%APPDATA%\UpworkProposalStrategist\`). It is never shown, shared, or written to logs.

> ⚠️ Never share your API key with anyone.

---

## For developers / packagers

Running from source and the zero-touch runtime design are documented in **[DEVELOPER.md](DEVELOPER.md)**. End users never need any of it.
