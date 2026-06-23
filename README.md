# GHFeedHook
This script reads the output from feedburner and sends a discord webhook if there is a update.

Name of resources functions as a link back to Galaxy Harvester. 

Requires Docker

Known Issue: the Feed burner source is a consolidation of all galaxies that use Galaxy Harvester, There is a limit to how many resources can be listed at any one time. There are per source feeds but it appears that flora and creature resources are no longer updating as of the end of 2025. I reached out to ioscode to report the issue, but they appear to still be outdated as of 2026/06

![example](example.png)

## 1. Clone the repo to your host
`git clone https://github.com/xyberviri/GHFeedHook/`

## 2. Copy the example env file
`cp .env.example .env`

## 3.  Fill in your webhook URL, Galaxy ID, & Galaxy name
`nano .env # set WEBHOOK_URL, GALAXY_ID, GALAXY_NAME`

## 4. Build the image
`docker compose build`

## 5. Start it (detached, auto-restarts on reboot)
`docker compose up -d`

# Other useful commands

## Watch live logs
`docker compose logs -f`

## Stop the bot
`docker compose down`

## Rebuild after changing the .py file
`docker compose up -d --build`
