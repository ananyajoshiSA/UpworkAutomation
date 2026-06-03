# Quick Start Guide

**Use this after your first time** (see `FIRST_TIME_SETUP_GUIDE.md`). It's the short version for using the tool again. If something doesn't work, open the full guide and check **Common Problems and Fixes**.

> 💡 **Tip:** There's no terminal and no setup to repeat — your AI key is already saved.

---

## Open the app

- Open the app folder and **double-click `Start Upwork Proposal Strategist`** (or its Desktop shortcut, if you made one).
- A small status window opens, then the app appears in your **web browser** after a few seconds. Leave that status window open while you use the app.

> 🔒 **Tip:** If Windows ever shows a firewall box about **"python"**, click **Allow access** — it's safe (the app only uses your own computer). Clicking Cancel works too.

> ℹ️ **Note:** The five pages are on the **left**: **Setup**, **Dossier**, **Job Screenshot**, **Analysis**, **Proposal**. They unlock in order (🔒 is normal).

---

## The 4 steps

### 1. Setup (only if needed)

Your AI key is saved from last time, so you can usually skip straight to step 2.

- If a page is locked, go to **Setup** and click **Run API Check**. Wait for `AI service is ready.`, then **Continue to Dossier**.
- Changing services or key? Update the fields, click **Save Configuration**, then **Run API Check**.

### 2. Dossier

- On **Dossier**, paste your work-files folder path into **Dossier folder path**.
- Click **Continue to Job Screenshot**.

> 💡 **Tip:** Get the path in File Explorer: hold **Shift**, right-click the folder → **Copy as path**.

### 3. Job Screenshot

- Take a screenshot of the Upwork job: **Windows + Shift + S**, drag a box around the whole post.
- On **Job Screenshot**, click **📋 Paste screenshot from clipboard** (or **Browse files** for a saved PNG/JPG).
- Click **Analyze Opportunity**. The app jumps to the Analysis.

> ⚠️ **Warning:** The **Groq** provider can't read screenshots. Use **OpenAI**, **Anthropic**, or **Gemini** as your **Vision Provider**.

### 4. Analysis → Proposal

- On **Analysis**, read the verdict — 🟢 **Apply Confidently** · 🟡 **Proceed With Caution** · 🔴 **Do Not Proceed** — plus your strengths and concerns. Click **Continue to Proposal**.
- On **Proposal**, click **Generate Proposal**, then **Copy Proposal** or **Download as .txt**.

> ℹ️ **Note:** If the verdict is 🔴 **Do Not Proceed**, tick **Yes, generate the proposal anyway** to enable the button.

> ✅ **Success:** You have a finished proposal draft. Review it, add your own voice, and send it on Upwork.

---

## When you're done

- Close the small **status window** that opened with the app (the one that says it's running). That quits the app. You can also close the browser tab.

> 💡 **Tip:** Never share your API key or your `.env` file. See **Safe Practices** in `FIRST_TIME_SETUP_GUIDE.md`.

---

## Mac, or running from source?

This is for the Windows app. To run from the source code (Mac/Linux or developers), see **[../DEVELOPER.md](../DEVELOPER.md)** — no conda or PYTHONPATH needed. The 4 steps above are the same once the app is open in your browser.
