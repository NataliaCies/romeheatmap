# 🚀 How to Publish Your Rome Climate Map Online
### A complete guide for non-technical users — no coding required

This guide will take your Rome Climate Map project and make it live on the internet.
By the end, you'll have a real website that anyone in the world can visit.

**Total time:** about 45–60 minutes
**Cost:** completely free

---

## What we're going to do (big picture)

Your project has two parts:
- **The map website** (the pretty thing people see) — already live on Netlify ✅
- **The backend** (the engine that fetches real satellite data) — this guide deploys it

Think of it like a restaurant: the frontend is the dining room customers see,
the backend is the kitchen doing the actual cooking.

---

## What you'll need

- A computer with internet
- An email address
- Your project files (the `rome-climate-backend` folder)
- About 1 hour of time

---

## PART 1 — Create your accounts (15 minutes)

You need accounts on 3 free services. Do these all first before anything else.

---

### Account 1: GitHub (stores your code)

GitHub is like Google Drive, but specifically for code.

1. Go to **https://github.com**
2. Click the green **"Sign up"** button
3. Enter your email, create a password, choose a username
4. Verify your email address (check your inbox)
5. When asked "How many team members?" choose **"Just me"**
6. When asked about features, you can skip everything
7. ✅ Done — you now have a GitHub account

---

### Account 2: Railway (runs your backend)

Railway is the service that will actually run your backend on the internet.

1. Go to **https://railway.app**
2. Click **"Login"** → choose **"Login with GitHub"**
3. It will ask permission to connect to GitHub — click **"Authorize railway-app"**
4. You're now logged in with your GitHub account
5. ✅ Done — Railway account created

---

### Account 3: Copernicus Data Space (real satellite data)

This is the European Space Agency's free service that provides the actual
Sentinel-2 satellite images of Rome.

1. Go to **https://dataspace.copernicus.eu**
2. Click **"Register"** in the top right corner
3. Fill in your name, email, create a password
4. Check your inbox and click the confirmation link
5. Log back in to confirm everything works
6. ✅ Done — you now have access to real satellite data

---

## PART 2 — Get your Copernicus API keys (10 minutes)

These "keys" are like passwords that let your backend talk to the satellite database.

1. Go to **https://dataspace.copernicus.eu** and log in
2. Click on your **name/avatar** in the top right corner
3. Click **"User settings"** or **"Profile"**
4. Look for a section called **"OAuth clients"** or **"API Access"**
5. Click **"+ Add client"** or **"Create new client"**
6. For the name, type: `rome-climate-api`
7. For type, select: **"Confidential"**
8. Click **Save** or **Create**
9. You'll see two codes appear:
   - **Client ID** — looks like: `sh-abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
   - **Client Secret** — looks like: `abc123XYZ...` (long random string)

⚠️ **IMPORTANT:** Copy both of these and save them somewhere safe RIGHT NOW.
The Client Secret is shown **only once** and cannot be recovered if lost.
Paste them into a notes app or text file on your computer.

---

## PART 3 — Upload your code to GitHub (10 minutes)

### Install GitHub Desktop (easiest way — no command line needed)

1. Go to **https://desktop.github.com**
2. Click **"Download for Mac"** or **"Download for Windows"**
3. Install it (just double-click and follow the prompts)
4. Open GitHub Desktop and sign in with your GitHub account

### Upload your project

1. In GitHub Desktop, click **"File"** → **"Add local repository"**
2. Click **"Choose..."** and navigate to your `rome-climate-backend` folder
3. If it says "This directory does not appear to be a Git repository",
   click **"create a repository"** instead
4. You'll see a list of all your project files with checkboxes
5. In the box at the bottom left that says "Summary", type: `First commit`
6. Click the blue **"Commit to main"** button
7. Then click **"Publish repository"** (blue button at the top)
8. A window appears — make sure **"Keep this code private"** is **unchecked**
   (Railway needs to see it)
9. Click **"Publish Repository"**
10. ✅ Your code is now on GitHub

**How to check it worked:**
Go to `https://github.com/YOUR_USERNAME/rome-climate-backend` in your browser.
You should see your project files listed there.

---

## PART 4 — Deploy the backend on Railway (10 minutes)

1. Go to **https://railway.app** and log in

2. Click the **"New Project"** button (purple, top right)

3. Select **"Deploy from GitHub repo"**

4. If it asks for permission to access GitHub, click **"Configure GitHub App"**
   and authorize Railway to see your repositories

5. You should see `rome-climate-backend` in the list — click it

6. Railway will start building your project automatically.
   You'll see a log with lots of text scrolling — this is normal!
   ⏱️ **Wait 5–8 minutes** for this to finish. Go make a coffee ☕

7. When you see a green checkmark or "Deploy successful", it's done

### Add Redis (the memory system)

Your backend needs Redis to remember things between requests.

1. In your Railway project dashboard, click the **"+ New"** button
2. Select **"Database"** → **"Add Redis"**
3. Railway sets everything up automatically in about 30 seconds
4. ✅ Done — your backend now has memory

---

## PART 5 — Add your secret keys to Railway (5 minutes)

This is where you tell Railway your Copernicus credentials and settings.

1. In your Railway project, click on your **API service**
   (the box that shows your `rome-climate-backend`, NOT the Redis one)
2. Click the **"Variables"** tab at the top
3. Click **"+ New Variable"** for each line below:

| Variable Name | Value to enter |
|---|---|
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `COPERNICUS_CLIENT_ID` | *(paste your Client ID from Part 2)* |
| `COPERNICUS_CLIENT_SECRET` | *(paste your Client Secret from Part 2)* |
| `ALLOWED_ORIGINS` | `https://vocal-marshmallow-7db69a.netlify.app` |
| `MAX_CLOUD_COVER_PCT` | `30` |
| `CACHE_TTL_WEATHER` | `1800` |
| `CACHE_TTL_SATELLITE` | `43200` |
| `CACHE_TTL_DISTRICTS` | `3600` |
| `TILE_SIZE_PX` | `512` |
| `ROME_BBOX_LON_MIN` | `12.35` |
| `ROME_BBOX_LAT_MIN` | `41.78` |
| `ROME_BBOX_LON_MAX` | `12.62` |
| `ROME_BBOX_LAT_MAX` | `41.98` |

4. After adding all variables, Railway automatically restarts your service
5. Wait about 2 minutes for it to restart

---

## PART 6 — Find your backend's web address (2 minutes)

Railway gives your backend a public URL.

1. In your Railway project, click on your API service
2. Click the **"Settings"** tab
3. Look for **"Domains"** or **"Public Networking"**
4. Click **"Generate Domain"** if nothing is there yet
5. You'll see a URL like: `rome-climate-backend-production-xxxx.up.railway.app`

📋 **Copy this URL — you'll need it in the next step.**

### Verify it's working

Open a new browser tab and go to:
```
https://YOUR_RAILWAY_URL.up.railway.app/api/v1/health
```
(replace `YOUR_RAILWAY_URL` with what Railway gave you)

You should see something like:
```json
{"status": "ok", "version": "1.0.0", ...}
```

If you see that — 🎉 **your backend is live on the internet!**

---

## PART 7 — Connect the map to the backend (5 minutes)

Now you need to tell your map website where the backend lives.

### Download your current map file

1. Go to your Netlify dashboard: **https://app.netlify.com**
2. Click on your site (`vocal-marshmallow-7db69a`)
3. Go to **"Deploys"** tab
4. Download the currently deployed `rome-climate-map.html` file
   (or use the copy you have on your computer)

### Edit one line in the file

Open `rome-climate-map.html` in a text editor.
- On **Mac**: right-click the file → "Open With" → TextEdit
- On **Windows**: right-click the file → "Open With" → Notepad

Press **Ctrl+F** (or Cmd+F on Mac) to open Find, and search for:
```
const API_BASE = '';
```

You'll find this line. Change it to:
```javascript
const API_BASE = 'https://YOUR_RAILWAY_URL.up.railway.app/api/v1';
```

(Replace `YOUR_RAILWAY_URL` with your actual Railway URL from Part 6)

Save the file.

### Upload the updated file to Netlify

1. Go to **https://app.netlify.com** and log in
2. Click on your site
3. Click the **"Deploys"** tab
4. Drag and drop your updated `rome-climate-map.html` file
   onto the deploy area (it says "Drag and drop your site output folder here")
5. Wait about 30 seconds
6. ✅ Your map is now connected to the real backend!

---

## PART 8 — Test your live website (5 minutes)

1. Go to: **https://vocal-marshmallow-7db69a.netlify.app**
2. The map should load as normal
3. In the bottom of the Legend panel, look at the small text:
   - 🟢 **"Sentinel-2 NDVI + LST (real satellite data)"** = everything is working perfectly
   - 🟡 **"Open-Meteo + UHI model"** = working, but using weather data instead of satellite
     (this is normal when there are clouds over Rome or the satellite hasn't passed recently)

4. Click on a district on the map — you should see real temperature data

---

## 🎉 You're done!

Your Rome Climate Monitor is now:
- **Live on the internet** at your Netlify URL
- **Powered by real satellite data** from ESA's Sentinel-2
- **Showing real weather** from Open-Meteo
- **Automatically updating** — no action needed from you

---

## What happens automatically from now on

Every night at midnight, the system:
- Fetches fresh satellite data for Rome
- Updates the temperature and vegetation maps
- Prepares tomorrow's forecast data

You don't need to do anything — it runs itself.

---

## If something goes wrong

### "I see an error on the map"
→ The backend is probably still starting up. Wait 2 minutes and refresh.

### "The map shows old/fake data"
→ Check that you saved the `API_BASE` line correctly in the HTML file.

### "Railway says build failed"
→ Go to Railway → your service → Deployments → click the failed deploy
  → look at the logs for a red error message → send it to your developer.

### "Copernicus credentials error"
→ Double-check you copied `CLIENT_ID` and `CLIENT_SECRET` exactly right in Railway variables.
  No spaces before or after the values.

### "I lost my Copernicus Client Secret"
→ Go back to dataspace.copernicus.eu → Profile → OAuth clients → delete the old one
  and create a new one. Update Railway variables with the new secret.

---

## Costs summary

| Service | Cost |
|---|---|
| Netlify (map hosting) | Free |
| Railway (backend) | Free up to 500 hours/month — enough for testing |
| Railway (after free tier) | ~$5/month |
| Copernicus satellite data | Always free |
| Open-Meteo weather | Always free |

**Total cost to run permanently: ~$5/month** (just Railway after the free tier)

---

## Quick reference — your URLs

Write these down:

| What | URL |
|---|---|
| Your map website | `https://vocal-marshmallow-7db69a.netlify.app` |
| Your backend API | `https://YOUR_RAILWAY_URL.up.railway.app` |
| Backend health check | `https://YOUR_RAILWAY_URL.up.railway.app/api/v1/health` |
| API documentation | `https://YOUR_RAILWAY_URL.up.railway.app/docs` |
| Copernicus dashboard | `https://dataspace.copernicus.eu` |
| Railway dashboard | `https://railway.app` |
| Netlify dashboard | `https://app.netlify.com` |
