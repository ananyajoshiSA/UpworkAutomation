# Upwork Proposal Strategist

A simple Windows app that helps you write better Upwork proposals, faster.

You give it **(1)** a folder of your work files and **(2)** a picture of an Upwork job. It tells you whether the job is worth applying to, and writes a first draft of your proposal — using only true facts from your files.

**Everything runs on your own computer. Your files stay with you.**

This README is the complete guide — open it, follow it top to bottom, and you're done. It spells out **every single click**.

---

## Contents

- [How to start](#how-to-start)
- [What it does](#what-it-does)
- [Before you begin](#before-you-begin)
- [Step-by-step (click by click)](#step-by-step-click-by-click)
- [Which AI service should I use?](#which-ai-service-should-i-use)
- [Your privacy](#your-privacy)
- [Common problems and fixes](#common-problems-and-fixes)
- [Safe practices](#safe-practices)
- [Uninstall](#uninstall)
- [Mac and developers](#mac-and-developers)

---

## How to start

> ## ▶ Double-click **`Start Upwork Proposal Strategist`**

That's the only thing you ever need to do. The **first** time, it sets itself up automatically (a few minutes, needs internet *that once*). After that it starts in seconds.

The app opens in your **web browser**. A small **black window** also stays open while it runs — that's normal; **keep it open while you work, and close it when you're done to quit.**

**No terminal. No installing Python. No setup steps. Ever.**

> 🆕 Brand new? Just follow [Step-by-step (click by click)](#step-by-step-click-by-click) below — it lists every click in order.

---

## What it does

Five simple pages, top to bottom on the **left**:

1. **Setup** — connect your AI service (one-time; it's remembered).
2. **Dossier** — point the app at a folder of your work files (remembered after the first time).
3. **Job Screenshot** — add a picture of the Upwork job.
4. **Analysis** — see the verdict: 🟢 Apply Confidently · 🟡 Proceed With Caution · 🔴 Do Not Proceed, plus your strengths and concerns.
5. **Proposal** — get a ready-to-copy proposal draft.

Each page unlocks after you finish the one before it (a 🔒 lock icon is normal).

---

## Before you begin

A short checklist:

- [ ] A **Windows 10 or 11** computer.
- [ ] The **app folder** — usually a `.zip` someone sent you (or downloaded from the project page).
- [ ] **Internet** — needed the first time so the app can set itself up.
- [ ] An **API key** — a secret password for an AI service. You get one in [Part B](#part-b--connect-your-ai-service-first-time-only) below.

You do **not** need to install Python or use a terminal — the app sets up everything it needs by itself, the first time you open it.

---

## Step-by-step (click by click)

Do the lines **in order**, top to bottom. **Each line is one thing to do.** Words in **bold** are the exact button or box to click.

> The **first time**, do every part (A → E). After that your AI key and your work-files folder are remembered, so you skip straight to taking a screenshot — see [Next time](#next-time) at the end.

### Part A — Open the app

1. If you were sent a **.zip**: right-click it → click **Extract All…** → click **Extract**. *(Skip if you already have a normal folder.)*
2. Open the folder and **double-click** the file named **Start Upwork Proposal Strategist**.
3. If a blue box says **"Windows protected your PC"**: click **More info**, then click **Run anyway**. *(Safe — it's just a new app.)*
4. A small **black window** opens. **The first time only**, it sets itself up for a few minutes — **wait** and do not close it. *(Needs internet this one time.)*
5. If Windows asks to allow **"python"** through the firewall: click **Allow access**. *(Safe — the app only talks to your own computer.)*
6. The app opens by itself in your **web browser**. **Leave the black window open** while you use the app.

### Part B — Connect your AI service *(first time only)*

> You only do Part B once. Next time your key is saved and you skip to [Part C](#part-c--point-to-your-work-files).

**First, get an API key** *(skip if you already have one)*:

1. Open your AI service's website and create a key, then **copy** it:
   - **OpenAI:** `https://platform.openai.com/api-keys`
   - **Anthropic:** `https://console.anthropic.com/`
   - **Gemini:** `https://aistudio.google.com/app/apikey`
   - **Groq:** `https://console.groq.com/keys`
2. ⚠️ The key is shown only once — copy it right away. These services can cost money based on use; check their pricing.

**Then, in the app on the Setup page:**

7. Click the **LLM Provider** box and choose your service (for example **openai**).
8. Leave the **LLM Model** box as it already is.
9. Click the **API Key** box and **paste your key**: press **Ctrl + V**.
10. Click the **Vision Provider** box and choose **same_as_llm**.
11. Click **Save Configuration**.
12. Click **Run API Check**.
13. **Wait** for the green message: **"AI service is ready."**
14. Click **Continue to Dossier**.

### Part C — Point to your work files

> Your "dossier" = one folder with your work files (résumé, portfolio, case studies, certificates…).

15. On the **Dossier** page, click the **Dossier folder path** box.
    - *If it's already filled in from last time:* check it's the right folder and **skip to step 17**.
16. **Paste your folder's path**: press **Ctrl + V**.
    - *How to get the path:* in File Explorer, hold **Shift**, right-click your folder, then click **Copy as path**.
17. Click **Continue to Job Screenshot**. **Wait** a few seconds.

### Part D — Add the job

18. Take a picture of the Upwork job: press **Windows + Shift + S** together, then **drag a box** around the whole job post.
19. Back in the app, on the **Job Screenshot** page, click **📋 Paste screenshot from clipboard**.
    - *If paste doesn't work:* click **Browse files** instead and pick a saved picture of the job (a `.png` or `.jpg`).
20. Click **Analyze Opportunity**. **Wait** — the app reads the job and moves you to the next page by itself.

### Part E — Read the result and get your proposal

21. You're now on the **Analysis** page. Read the verdict — 🟢 **Apply Confidently** · 🟡 **Proceed With Caution** · 🔴 **Do Not Proceed** — and the reasons.
22. Click **Continue to Proposal**.
23. On the **Proposal** page, click **Generate Proposal**. **Wait** for the draft to appear in the box.
    - *If the verdict was 🔴 and the button is greyed out:* first tick the checkbox **Yes, generate the proposal anyway**, then click **Generate Proposal**.
24. Click **Copy Proposal** *(or click **Download as .txt** to save it as a file)*.
25. Go to Upwork, click in your proposal box, and **paste**: press **Ctrl + V**. Read it, add your own voice, then send it.

### When you're finished

26. Close the small **black window** (the one that opened with the app). That quits the app. *(You can also close the browser tab.)*

### Next time

- Do **Part A** (open the app) — instant now, no setup wait.
- **Skip Part B** — your AI key is saved.
- On the **Dossier** page, your folder is already filled in → just click **Continue to Job Screenshot**.
- Do **Part D** and **Part E**.

---

## Which AI service should I use?

You only need **one**:

- **OpenAI** — popular and easy. A good first choice. Can read pictures.
- **Anthropic** — makes the "Claude" AI. Can read pictures.
- **Gemini** — Google's AI. Can read pictures.
- **Groq** — fast and cheap, but **cannot read pictures**.

> ⚠️ The **Job Screenshot** step needs an AI that can read pictures. Use **OpenAI**, **Anthropic**, or **Gemini** — **not Groq**.

---

## Your privacy

- Your original files **never leave your computer**. Only a small, relevant bit of text is sent to the AI service.
- Your secret key is saved in a private file on your own computer. It's never shown, shared, or written to logs.

> ⚠️ Never share your API key with anyone.

---

## Common problems and fixes

**The browser didn't open on its own.**
The black window shows an address like `http://127.0.0.1:8xxx`. Open your browser (Chrome, Edge, Firefox…) and type that address into the bar. The **first** launch is just slow (it's installing) — give it a few minutes, and make sure you have internet that first time.

**"Windows protected your PC".**
Expected for a new app that isn't signed yet. Click **More info → Run anyway**. It is safe.

**A "Windows Firewall" box about "python".**
Normal on the first run — the app runs a tiny server on **your own computer** to show its pages in your browser. Click **Allow access**. Nothing is opened to the internet; even if you click **Cancel**, the app still works.

**Double-clicking does nothing / the launcher is blocked.**
If antivirus blocked it, open the app folder → go into the **`scripts`** folder → double-click **`run.bat`** (it does exactly the same thing). If files seem to be missing, antivirus may have removed them — re-download the folder, **allow** it when prompted, and fully **Extract All** before opening.

**"The app's runtime files are missing".**
The folder wasn't fully unzipped, or antivirus removed files. Re-download / **Extract All** the whole folder again and allow it in your antivirus, then launch again.

**API not working.**
Re-copy your key (watch for extra spaces). Make sure the **LLM Provider** matches your key. Check your internet and your account credit. Then click **Save Configuration**, then **Run API Check** again.

**Screenshot not working.**
Use a **PNG, JPG, JPEG, or WEBP** picture, **20 MB or smaller**. **Groq cannot read pictures** — on **Setup**, set **Vision Provider** to OpenAI, Anthropic, or Gemini. If the **📋 Paste** button doesn't catch your screenshot, save it as a file and use **Browse files**.

**Change or remove my saved key.**
Go to **Setup**. Type a new key and **Save Configuration**, or click **Clear Credentials** to wipe the saved key.

**Start over completely.**
Close the app. Open **File Explorer**, click the address bar at the top, type `%APPDATA%\UpworkProposalStrategist` and press **Enter** — it opens the app's private folder. Delete that folder (this removes your saved key, your remembered dossier folder, and logs). Open the app again to set up fresh.

---

## Safe practices

- **Never share your API key.** It's a secret password — anyone with it can spend your money. Never put it in chats, emails, or screenshots. If it leaks, delete it on the provider's website and make a new one.
- **Never share the hidden `.env` file** (it lives in `%APPDATA%\UpworkProposalStrategist\` and holds your key).
- Keep a spare copy of your **dossier folder**, and save any proposal drafts you like.

---

## Uninstall

Double-click **`Uninstall Upwork Proposal Strategist`**, type **YES**, and press Enter. It cleanly removes your saved settings + API key, the firewall allowance, any Desktop shortcut, and the whole app folder — nothing is left behind (the app adds no registry entries and nothing to Program Files).

---

## Mac and developers

This app is for **Windows**. To run from the source code (Mac/Linux or developers), build the shippable zip, or understand how the bundled-Python design works, see **[DEVELOPER.md](DEVELOPER.md)**. End users never need any of it.
