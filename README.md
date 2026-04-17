# GHFeedHook
This script reads the output from feedburner and sends a discord webhook if there is a update.

# 1. Copy the example env file and fill in your webhook URL + galaxy ID
cp .env.example .env
nano .env          # set WEBHOOK_URL, GALAXY_ID, GALAXY_NAME

# 2. Build the image
docker compose build

# 3. Start it (detached, auto-restarts on reboot)
docker compose up -d

# Watch live logs
docker compose logs -f

# Stop the bot
docker compose down

# Rebuild after changing the .py file
docker compose up -d --build
