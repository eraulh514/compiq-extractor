# CompIQ PDF Extractor — Setup & Deployment Guide

## What You Have
- `backend/app.py` — Python Flask server that handles PDF extraction and Excel export
- `backend/requirements.txt` — Python package dependencies
- `frontend/index.html` — The full CompIQ website (landing page + working tool)
- `render.yaml` — One-click deployment config for Render

---

## Step 1: Get Your Anthropic API Key
1. Go to https://console.anthropic.com
2. Sign in (or create an account)
3. Click "API Keys" in the left sidebar
4. Click "Create Key" — copy it and save it somewhere safe
   - It looks like: `sk-ant-api03-...`

---

## Step 2: Upload to GitHub
You need a free GitHub account to deploy to Render.

1. Go to https://github.com and create an account if you don't have one
2. Click the "+" icon → "New repository"
3. Name it `compiq-extractor`, set it to **Public**, click "Create repository"
4. On your desktop, open Terminal (Mac) or Command Prompt (Windows)
5. Run these commands one by one:

```
cd ~/Desktop/PDF\ Extractor\ Project
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/compiq-extractor.git
git push -u origin main
```
(Replace YOUR_USERNAME with your actual GitHub username)

---

## Step 3: Deploy to Render (Free)
1. Go to https://render.com and sign up with your GitHub account
2. Click "New +" → "Web Service"
3. Connect your GitHub repo: select `compiq-extractor`
4. Render will auto-detect the `render.yaml` file
5. Under "Environment Variables", click "Add Environment Variable":
   - Key: `ANTHROPIC_API_KEY`
   - Value: paste your API key from Step 1
6. Click "Create Web Service"
7. Wait 3–5 minutes for the first deploy
8. Your live URL will appear at the top — it looks like:
   `https://compiq-extractor.onrender.com`

---

## Step 4: Connect Frontend to Backend
After you have your Render URL:

1. Open `frontend/index.html` in a text editor (Notepad, TextEdit, VS Code)
2. Find this line near the bottom (around line 300):
   ```
   const API_BASE = window.location.hostname === 'localhost'...
   ```
3. Change it to just:
   ```javascript
   const API_BASE = 'https://compiq-extractor.onrender.com';
   ```
   (Use your actual Render URL)
4. Save the file

Now you can open `frontend/index.html` in Chrome and it will work!

---

## Step 5: (Optional) Host the Frontend Too
To give your boss a single link:

**Option A — Netlify Drop (free, 30 seconds):**
1. Go to https://app.netlify.com/drop
2. Drag your `frontend/index.html` file onto the page
3. Get a URL like `https://amazing-name-123.netlify.app`

**Option B — Add frontend to Render:**
1. Copy `frontend/index.html` into `backend/static/index.html`
2. In `backend/app.py`, add this route:
   ```python
   from flask import send_from_directory
   
   @app.route('/')
   def serve_frontend():
       return send_from_directory('static', 'index.html')
   ```
3. Push to GitHub — Render will redeploy automatically

---

## Testing Locally (Optional)
If you want to test before deploying:

1. Install Python 3.10+ from https://python.org
2. Open Terminal in the `PDF Extractor Project` folder
3. Run:
```
pip install -r backend/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
python backend/app.py
```
4. Open `frontend/index.html` in Chrome
5. The tool will use `http://localhost:5000` automatically

---

## How It Works
1. You drop a PDF on the website
2. The website sends it to your Render server
3. The server extracts all text from every page (using PyMuPDF)
4. The text is sent to Claude AI for structured extraction
5. Claude returns a JSON array of all comp rows
6. The website displays them in a table
7. You click "Download Excel" — the server builds and sends a formatted .xlsx file

## Speed
- ~15–30 seconds per PDF (limited by Claude AI response time)
- The server runs 24/7 on Render — no computer needed

## Cost
- Render free tier: $0/month (spins down after 15min inactivity, wakes up on next request)
- Anthropic API: ~$0.01–0.05 per PDF depending on size
- If you need faster cold starts, Render Starter plan is $7/month

---

## Troubleshooting

**"Failed to fetch" error:**
→ Your API_BASE URL in index.html doesn't match your Render URL. Double-check it.

**"Extraction failed" error:**
→ Check your ANTHROPIC_API_KEY is set correctly in Render environment variables.

**Render deploy fails:**
→ Make sure your GitHub repo has the `render.yaml` file at the root level.

**Empty extraction:**
→ Some PDFs are fully image-based with no extractable text. The tool handles these 
   via Claude's vision — if it still fails, the PDF may be corrupted or password-protected.
