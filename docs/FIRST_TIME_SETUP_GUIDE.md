# First Time Setup Guide

This guide is for people who have **never** done anything like this before. That's okay. Just follow the steps in order. Do one, then go to the next.

The main way to use this tool is the **Windows app**: you **double-click one file** and it opens in your **web browser**. The very first time, it sets itself up automatically (one time, a few minutes, needs internet). **No terminal. No installing anything yourself. No setup commands.**

> 💡 **Tip:** Do not skip steps. Do them top to bottom: 1, 2, 3…

> ℹ️ **On a Mac, or a developer?** This guide is for the Windows app. Running from the source code is covered in [DEVELOPER.md](../DEVELOPER.md); the in-app steps (3–8) are the same.

---

## How to read this guide

- A **button** or thing you click is shown in **bold**.
- After each step, the guide tells you **what you should see**.
- 📷 means a picture can be added there later.

---

## Table of Contents

- [What This Tool Does](#what-this-tool-does)
- [Things You Need Before Starting](#things-you-need-before-starting)
- [Step 1: Open the App (first time)](#step-1-open-the-app-first-time)
- [Step 2: Opening It Again](#step-2-opening-it-again)
- [Step 3: Connect Your AI Service](#step-3-connect-your-ai-service)
- [Step 4: Run the API Check](#step-4-run-the-api-check)
- [Step 5: Add Your Dossier](#step-5-add-your-dossier)
- [Step 6: Add the Job Screenshot](#step-6-add-the-job-screenshot)
- [Step 7: Read the Analysis](#step-7-read-the-analysis)
- [Step 8: Generate Your Proposal](#step-8-generate-your-proposal)
- [Common Problems and Fixes](#common-problems-and-fixes)
- [Safe Practices](#safe-practices)
- [Mac, or Running From Source](#mac-or-running-from-source)

---

## What This Tool Does

This tool is the **Upwork Proposal Strategist**.

On Upwork, people send a message called a **proposal** to win freelance jobs. This tool helps you write a good proposal fast.

Here's what it does:

1. You give it a folder with your work files (resume, past projects, and so on).
2. You give it a **picture** of an Upwork job.
3. It compares the job to your files.
4. It gives you a clear **verdict**: 🟢 Apply Confidently · 🟡 Proceed With Caution · 🔴 Do Not Proceed.
5. It writes a first draft of your proposal, using only true facts from your files.

> 💡 **Tip:** Everything runs on your own computer. Your files stay with you.

---

## Things You Need Before Starting

A short checklist. The guide shows you how to get each one.

- [ ] **A Windows 10 or 11 computer.**
- [ ] **The app folder** — usually a ZIP file someone sent you, named something like `UpworkProposalStrategist.zip`.
- [ ] **Internet** (Wi-Fi or cable) — needed the first time so the app can set itself up.
- [ ] **An API key** — a secret password for an AI service. You get one in **Step 3**.

> ℹ️ **Note:** You do **not** need to install Python or open a terminal. The app sets up everything it needs by itself, the first time you open it.

---

## Step 1: Open the App (first time)

**Do this:**

1. If you were sent a **ZIP**, right-click it → **Extract All…** → **Extract**. You now have a folder (for example `UpworkProposalStrategist`).
2. Open that folder and **double-click** the file named **`Start Upwork Proposal Strategist`**.
3. Windows may show a blue box (**"Windows protected your PC"**). This is normal for a new app that isn't signed yet.
   - Click **More info**, then **Run anyway**.
4. A small **black setup window** appears and says it's doing a **one-time setup**. It works for a few minutes. **Leave it alone until it finishes** — it's downloading and installing everything the app needs, just this once.

**You should see:** After setup, the app opens in your **web browser** on the **Setup** page. The small black window stays open behind it — that's normal; leave it open while you use the app.

> ✅ **Success:** The app is open in your browser. Continue to **Step 3**.

> 💡 **Tip:** The first time is the slow time (a few minutes, needs internet). Every launch after that takes only a few seconds.

> 🔒 **If Windows asks about the firewall:** A box may appear saying *"Windows Defender Firewall has blocked some features of python."* Click **Allow access** (you can leave **Public networks** unchecked). It's safe — the app only talks to **your own computer**. Even if you click **Cancel**, the app still works.

> ℹ️ **Note:** Five pages run down the **left side**: **Setup**, **Dossier**, **Job Screenshot**, **Analysis**, **Proposal**. A lock (🔒) is normal — each unlocks when you finish the one before it.

> ⚠️ **If the browser doesn't open on its own:** the black window shows a web address like `http://127.0.0.1:…` — type that into your browser. See [Common Problems → App won't open](#common-problems-and-fixes).

> 📷 *Screenshot placeholder: the "one-time setup" window, then the app on Setup.*

---

## Step 2: Opening It Again

Next time you want to use the tool:

- Open the app folder and **double-click `Start Upwork Proposal Strategist`** again.
- No setup this time — it opens in your browser in a few seconds.

> 💡 **Tip:** To make it easier later, right-click **`Start Upwork Proposal Strategist`** → **Send to → Desktop (create shortcut)**. Then it's one click from your Desktop.

> ✅ **Success:** The app opens in your browser. Your AI key from last time is remembered, so you can usually go straight to **Step 5**.

---

## Step 3: Connect Your AI Service

Now you tell the app which AI service to use and give it your secret key. This is on the **Setup** page. You only do this **once** — the app remembers it next time.

### Pick an AI service

You only need **one**:

- **OpenAI** — popular and easy. A good first choice. Can read pictures.
- **Anthropic** — makes the "Claude" AI. Can read pictures.
- **Gemini** — Google's AI. Can read pictures.
- **Groq** — fast and cheap, but **cannot read pictures** (so the screenshot step won't work).

> ✅ **Recommended:** Pick **OpenAI**, **Anthropic**, or **Gemini** so screenshots work.

### Get a key

1. Open the website for your service:
   - **OpenAI:** `https://platform.openai.com/api-keys`
   - **Anthropic:** `https://console.anthropic.com/`
   - **Gemini:** `https://aistudio.google.com/app/apikey`
   - **Groq:** `https://console.groq.com/keys`
2. Sign up or log in.
3. Click a button like **Create new secret key**.
4. **Copy** the key (a long line of letters and numbers).

> ⚠️ **Warning:** The key is shown only once — copy it right away. These services can cost money based on use; check their pricing.

### Fill in the Setup page

The service names are lowercase: `openai`, `anthropic`, `groq`, `gemini`.

1. In **LLM Provider**, pick your service.
2. In **LLM Model**, leave the one already filled in.
3. Paste your key into the **API Key** box. ("Paste" = press **Ctrl + V**.)
4. In **Vision Provider**, pick **`same_as_llm`** (this reuses your choice, so you only need one key).
5. Click **Save Configuration**.

**You should see:** A message like `Configuration saved. Run API Check to continue.`

> ⚠️ **Warning:** Do not pick **Groq** for **Vision Provider** — it cannot read pictures.

> 📷 *Screenshot placeholder: the Setup page with the fields filled in.*

---

## Step 4: Run the API Check

This tests your key.

**Do this:** Click the **Run API Check** button.

**You should see (good):** A green message: `AI service is ready.` A **Continue to Dossier** button appears.

> ✅ **Success:** You see `AI service is ready.` Click **Continue to Dossier**.

**You should see (problem):** A red message, like `The API key or model could not be validated.`

If you see a red message, check these:

- The key was copied with no extra spaces.
- The **LLM Provider** matches your key (an OpenAI key needs OpenAI).
- Your internet works, and your account has credit.

Then click **Save Configuration** again, and **Run API Check** again.

---

## Step 5: Add Your Dossier

A **dossier** is just your folder of work files. The app reads it as proof of what you can do.

### Put files in one folder

Make one folder and put your work files in it. The app can read these types:

- PDF (`.pdf`), Word (`.docx`), Text (`.txt`), Markdown (`.md`)
- Data (`.json`, `.csv`)
- Images (`.png`, `.jpg`, `.jpeg`)

Good things to add: resume / CV, portfolio, testimonials or reviews, case studies, certifications, work samples.

> 💡 **Tip:** To try it the first time, use the folder named `sample_dossier` that came with the project.

> ⚠️ **Warning:** Do not put private files (passwords, bank files) in this folder.

### Get the folder's path

A **path** is the full address of a folder. In **File Explorer**, hold **Shift**, right-click the folder, and choose **Copy as path**.

> 💡 **Tip:** Folders with spaces in the name (like `My Work Files`) are fine.

### Do it in the app

On the **Dossier** page:

1. Paste the path into the **Dossier folder path** box (**Ctrl + V**).
2. Click **Continue to Job Screenshot**.

The app reads your files behind the scenes and moves you to the next page.

> ✅ **Success:** You land on the **Job Screenshot** page.

> ⚠️ **Warning:** If you see "Couldn't read that folder", the path was wrong or the folder had no readable files. Re-copy the path and try again.

> 📷 *Screenshot placeholder: the Dossier page.*

---

## Step 6: Add the Job Screenshot

A **screenshot** is a picture of your screen. Take a picture of the Upwork job you want.

### Take the picture

- Press **Windows + Shift + S**. Drag a box around the whole job post.
- The picture is now copied, ready to paste.

> 💡 **Tip:** Capture the whole job post — title, description, skills, and budget.

### Add it in the app

On the **Job Screenshot** page, you have **two easy ways**:

- **Paste it:** click **📋 Paste screenshot from clipboard** (works right after the step above). *— easiest*
- **Or upload a file:** click **Browse files** and pick a saved picture. Types: **PNG, JPG, JPEG, WEBP**, each **20 MB or smaller**.

Then:

1. Check it says a screenshot is ready.
2. Click **Analyze Opportunity**.

The app reads the job from your picture and goes straight to the **Analysis** page.

> ✅ **Success:** The app reads the picture and shows the Analysis.

> ⚠️ **Warning:** If you picked **Groq**, this step won't work. Go back to **Setup** and pick OpenAI, Anthropic, or Gemini.

> 💡 **Tip:** If the **📋 Paste** button doesn't pick up your screenshot, save it as a PNG/JPG and use **Browse files** instead — that always works.

> 📷 *Screenshot placeholder: the Job Screenshot page.*

---

## Step 7: Read the Analysis

The **Analysis** page compares the job to your files and shows a clear verdict.

**You should see:**

1. A **Verdict** — one of:
   - 🟢 **Apply Confidently** — a good match; go for it.
   - 🟡 **Proceed With Caution** — possible, but check the concerns first.
   - 🔴 **Do Not Proceed** — likely not worth your connects.
2. A few **short reasons** for the verdict.
3. **Key strengths** — why you fit.
4. **Concerns** — what may count against you.
5. Sometimes a **Heads up** card naming job details that weren't visible in the picture.

**Do this:** Read the results. When ready, click **Continue to Proposal**.

> 💡 **Tip:** Click **Re-run Analysis** at the top if you want a fresh result.

> 📷 *Screenshot placeholder: the Analysis page with the verdict.*

---

## Step 8: Generate Your Proposal

The **Proposal** page writes your proposal draft, using only facts from your files.

**Do this:**

1. Click **Generate Proposal**.
2. Wait a moment for the draft to appear in the box.
3. Click **Copy Proposal** (to copy) or **Download as .txt** (to save). Paste it into Upwork.

> ℹ️ **Note:** If the verdict was 🔴 **Do Not Proceed**, the button is off. To force a draft anyway, first tick **Yes, generate the proposal anyway**.

> 💡 **Tip:** This is a first draft. Read it, add your own voice, and fix anything before you send it.

> ✅ **Success:** Your proposal draft is ready.

> 📷 *Screenshot placeholder: the Proposal page with a finished draft.*

---

## When You're Done

- Close the small **black status window** that opened with the app (the one that says it's running). That quits the app — there's nothing else to stop. You can also close the browser tab.
- Your AI settings are saved, so next time you only do Steps 5–8.

> 💡 **Tip:** Next time, use the shorter [QUICK_START_GUIDE.md](QUICK_START_GUIDE.md) in this same `docs` folder.

---

## Common Problems and Fixes

### Browser didn't open on its own

- The small **black window** shows a web address like `http://127.0.0.1:8xxx`. Open your browser (Chrome, Edge, Firefox…) and type that address into the bar.
- The **first** launch is just slow (it's installing — give it a few minutes, and make sure you have internet that first time). Later launches take only seconds.
- If you closed the black window, the app stops. Double-click **`Start Upwork Proposal Strategist`** again to reopen it.

### "Windows protected your PC"

- Expected for a new app that isn't signed yet. Click **More info → Run anyway**. It is safe.

### A "Windows Firewall" box about "python"

- Normal on the first run. To show its pages in your browser, the app runs a tiny server on **your own computer** — Windows just wants you to okay it. Click **Allow access** (you can leave **Public networks** unchecked).
- It's safe: nothing is opened to the internet. The server only listens on your own machine (`127.0.0.1`), so even if you click **Cancel**, the app keeps working.

### Double-clicking does nothing / the launcher is blocked

- If antivirus blocked the launcher, open the app folder, go into the **`scripts`** folder, and double-click **`run.bat`** instead — it does exactly the same thing.
- If files seem to be missing, your antivirus may have removed them. Re-download the folder, **allow** it when prompted, and fully **Extract All** before opening.

### API not working

- Re-copy your key (watch for extra spaces).
- Make sure the **LLM Provider** matches your key.
- Check your internet and your account credit.
- Click **Save Configuration**, then **Run API Check** again.

### Screenshot not working

- Use a **PNG, JPG, JPEG, or WEBP** picture, **20 MB or smaller**.
- **Groq cannot read pictures.** On **Setup**, set **Vision Provider** to OpenAI, Anthropic, or Gemini.
- If the **📋 Paste** button doesn't catch your screenshot, save it as a file and use **Browse files**.

### I need to change or remove my saved key

- Go to **Setup**. Type a new key and **Save Configuration**, or click **Clear Credentials** to wipe the saved key.
- Your key is stored privately in `%APPDATA%\UpworkProposalStrategist\.env`.

### Start over completely

- Close the app. Then open **File Explorer**, click the address bar at the top, type `%APPDATA%\UpworkProposalStrategist` and press **Enter** — it opens the app's private folder. Delete that folder (this removes your saved key and logs). Open the app again to set up fresh.

### Uninstall the app completely

- Close the app, then double-click **`Uninstall Upwork Proposal Strategist`** in the app folder. Type **YES** and press **Enter**.
- It removes your saved key + logs, the firewall allowance, any Desktop shortcut, **and** the whole app folder — a clean, complete removal with nothing left behind.

---

## Safe Practices

> ⚠️ **Warning:** Follow these to stay safe.

### Never share your API key

- It's a secret password. Anyone with it can spend your money.
- Never put it in chats, emails, or screenshots.
- If it leaks, delete it on the provider's website and make a new one.

### Never share the `.env` file

- The app saves your key in a hidden file named `.env` (in `%APPDATA%\UpworkProposalStrategist\` for the installed app).
- Never send this file to anyone or put it online.

### Keep backup copies

- Keep a spare copy of your **dossier folder**.
- Save any proposal drafts you like.

---

## Mac, or Running From Source

This guide is for the **Windows app**. If you're on a **Mac/Linux**, or you're a developer who wants to run the project's source code, see **[DEVELOPER.md](../DEVELOPER.md)**. It explains how to start the app (no conda and no PYTHONPATH needed). Once the app is open in your browser, the in-app steps **3–8 above are identical**.

> ℹ️ **Note (for whoever hands this out):** To package it so end users just double-click, see [DEVELOPER.md](../DEVELOPER.md).
