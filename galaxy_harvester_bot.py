#!/usr/bin/env python3
"""
Galaxy Harvester Discord Bot
Polls the Galaxy Harvester FeedBurner RSS feed and sends Discord webhook
announcements when new resources appear for a configured galaxy.

Usage:
    python galaxy_harvester_bot.py

Configuration via environment variables or config.json (see below).
"""

import os
import re
import json
import time
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────
# Configuration  (edit here OR use env vars)
# ──────────────────────────────────────────────
CONFIG_FILE = "config.json"          # optional override file

DEFAULTS = {
    "WEBHOOK_URL":   "",             # Discord webhook URL  (required)
    "GALAXY_ID":     "68",          # Galaxy ID to filter (string match vs link)
    "GALAXY_NAME":   "SWG Infinity",# Human-readable name shown in embeds
    "FEED_URL":      "https://feeds.feedburner.com/GalaxyHarvesterResourceActivity",
    "POLL_INTERVAL": 300,            # seconds between polls (default 5 min)
    "STATE_FILE":    "gh_state.json",# persisted seen-resource store
    "LOG_LEVEL":     "INFO",
}

# ──────────────────────────────────────────────
# Stat / colour constants
# ──────────────────────────────────────────────
STAT_LABELS = {
    "CR": "Cold Resistance",
    "CD": "Conductivity",
    "DR": "Decay Resistance",
    "FL": "Flavor",
    "HR": "Heat Resistance",
    "MA": "Malleability",
    "PE": "Potential Energy",
    "OQ": "Overall Quality",
    "SR": "Shock Resistance",
    "UT": "Unit Toughness",
    "ER": "Entangle Resistance",
}
STAT_KEYS = list(STAT_LABELS.keys())

GROUP_COLORS = {
    "iron":                  0xA0522D,
    "steel":                 0x708090,
    "aluminum":              0xB0C4DE,
    "copper":                0xB87333,
    "ore_carbonate":         0x8B7355,
    "ore_extrusive":         0xFF4500,
    "ore_intrusive":         0xCD853F,
    "radioactive":           0x7CFC00,
    "gemstone":              0x9400D3,
    "gemstone_armophous":    0x9400D3,
    "fuel_petrochem_liquid": 0x1E90FF,
    "fuel_petrochem_solid":  0x4169E1,
    "chemical":              0x00CED1,
    "fiberplast":            0x228B22,
    "hide":                  0xD2691E,
    "bone":                  0xFFFACD,
    "horn":                  0xDAA520,
    "milk":                  0xFFFFF0,
    "meat":                  0xFF6347,
    "seafood":               0x20B2AA,
    "flora_food":            0x32CD32,
    "flora_structural":      0x556B2F,
    "water":                 0x00BFFF,
    "wind":                  0xADD8E6,
    "solar":                 0xFFD700,
}
DEFAULT_COLOR = 0x5865F2  # Discord blurple

# ──────────────────────────────────────────────
# Helpers: config loading
# ──────────────────────────────────────────────

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    # Override from config.json if present
    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE) as f:
            file_cfg = json.load(f)
        cfg.update(file_cfg)
    # Override from environment variables
    for key in DEFAULTS:
        val = os.environ.get(key)
        if val is not None:
            cfg[key] = val
    # Cast numeric types
    cfg["POLL_INTERVAL"] = int(cfg["POLL_INTERVAL"])
    cfg["GALAXY_ID"] = str(cfg["GALAXY_ID"])
    return cfg


# ──────────────────────────────────────────────
# Helpers: state persistence
# ──────────────────────────────────────────────

def load_state(state_file: str) -> dict:
    """Return dict of {resource_name: resource_data} currently tracked."""
    if Path(state_file).exists():
        with open(state_file) as f:
            return json.load(f)
    return {}


def save_state(state_file: str, state: dict) -> None:
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


# ──────────────────────────────────────────────
# Helpers: feed parsing
# ──────────────────────────────────────────────

def fetch_feed(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "GHDiscordBot/1.0"})
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logging.warning(f"Feed fetch error: {e}")
        return None


def parse_stats(encoded_html: str) -> dict:
    """Extract stat key/value pairs from <content:encoded> HTML fragment."""
    stats = {}
    # Match patterns like "OQ: 947 (95%)" or "DR: 456 (46%)"
    for match in re.finditer(r'\b([A-Z]{2}):\s*(\d+)\s*\(\d+%\)', encoded_html):
        key, value = match.group(1), int(match.group(2))
        if key in STAT_LABELS:
            stats[key] = value
    return stats


def guess_group_from_type(type_name: str) -> str:
    """
    Best-effort group detection from a resource type name like
    'Corintium Intrusive Ore' → 'ore_intrusive'.
    Falls back to empty string so DEFAULT_COLOR is used.
    """
    name_lower = type_name.lower()
    for group in GROUP_COLORS:
        # Convert group key underscores to spaces and check substring
        if group.replace("_", " ") in name_lower:
            return group
        # Also check individual words
        parts = group.split("_")
        if all(p in name_lower for p in parts if len(p) > 2):
            return group
    # Simple keyword fallbacks
    keyword_map = {
        "iron":       "iron",
        "steel":      "steel",
        "aluminum":   "aluminum",
        "copper":     "copper",
        "carbonate":  "ore_carbonate",
        "extrusive":  "ore_extrusive",
        "intrusive":  "ore_intrusive",
        "radioactive":"radioactive",
        "gem":        "gemstone",
        "petrochem":  "fuel_petrochem_liquid",
        "chemical":   "chemical",
        "fiberplast": "fiberplast",
        "hide":       "hide",
        "bone":       "bone",
        "horn":       "horn",
        "milk":       "milk",
        "meat":       "meat",
        "seafood":    "seafood",
        "vegetable":  "flora_food",
        "fruit":      "flora_food",
        "wood":       "flora_structural",
        "lumber":     "flora_structural",
        "water":      "water",
    }
    for kw, group in keyword_map.items():
        if kw in name_lower:
            return group
    return ""


def parse_feed_items(xml_text: str, galaxy_id: str) -> dict:
    """
    Parse RSS XML and return dict of resources belonging to galaxy_id.
    Key = resource name (lowercased), value = resource metadata dict.
    """
    resources = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logging.error(f"XML parse error: {e}")
        return resources

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}

    for item in root.findall(".//item"):
        link_el = item.find("link")
        if link_el is None or link_el.text is None:
            continue

        link = link_el.text.strip()

        # Filter by galaxy_id: link looks like /resource.py/68/resourcename
        pattern = rf"/resource\.py/{re.escape(galaxy_id)}/(\w+)"
        m = re.search(pattern, link)
        if not m:
            continue

        resource_name = m.group(1)

        # Parse title: "<name> Added by <user> on <galaxy>"
        title_el = item.find("title")
        title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
        added_by = ""
        title_match = re.match(r"^(\S+)\s+Added by\s+(.+?)\s+on\s+(.+)$", title_text, re.IGNORECASE)
        if title_match:
            added_by = title_match.group(2)

        # Resource type from <description>
        desc_el = item.find("description")
        resource_type = ""
        if desc_el is not None and desc_el.text:
            resource_type = desc_el.text.strip()

        # Stats from <content:encoded>
        encoded_el = item.find("content:encoded", ns)
        stats = {}
        if encoded_el is not None and encoded_el.text:
            stats = parse_stats(encoded_el.text)

        pub_date = ""
        pub_el = item.find("pubDate")
        if pub_el is not None and pub_el.text:
            pub_date = pub_el.text.strip()

        group = guess_group_from_type(resource_type)

        resources[resource_name] = {
            "name":          resource_name,
            "resource_type": resource_type,
            "group":         group,
            "added_by":      added_by,
            "link":          link,
            "pub_date":      pub_date,
            "stats":         stats,
        }

    return resources


# ──────────────────────────────────────────────
# Helpers: Discord embed
# ──────────────────────────────────────────────

def pub_date_to_iso(pub_date: str) -> str:
    """
    Convert an RSS pubDate like 'Sun, 12 Apr 2026 17:12:52 -0800'
    to a UTC ISO 8601 string Discord accepts: '2026-04-13T01:12:52+00:00'.
    Falls back to the current UTC time if parsing fails.
    """
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        logging.debug(f"Could not parse pub_date '{pub_date}', falling back to utcnow.")
        return datetime.now(timezone.utc).isoformat()


def build_embed(resource: dict, galaxy_name: str) -> dict:
    group  = resource.get("group", "")
    color  = GROUP_COLORS.get(group, DEFAULT_COLOR)
    stats  = resource.get("stats", {})
    rtype  = resource.get("resource_type", "Unknown Type")
    name   = resource.get("name", "unknown")
    link   = resource.get("link", "")
    added_by = resource.get("added_by", "")
    pub_date = resource.get("pub_date", "")

    # Build stat fields — only stats that are present
    fields = []
    for key in STAT_KEYS:
        if key in stats:
            pct = round(stats[key] / 10)  # 0-1000 → 0-100%
            bar = build_bar(pct)
            fields.append({
                "name":   f"{STAT_LABELS[key]} ({key})",
                "value":  f"`{bar}` {stats[key]} ({pct}%)",
                "inline": False,
            })

    embed = {
        "title":       f"🆕 {name}",
        "description": f"**{rtype}**",
        "url":         link,
        "color":       color,
        "fields":      fields,
        "footer": {
            "text": f"{galaxy_name} • Added by {added_by}" if added_by else galaxy_name
        },
        "timestamp":   pub_date_to_iso(pub_date),
    }
    return embed


def build_bar(pct: int, length: int = 10) -> str:
    """Build a simple ASCII progress bar for a 0–100 percentage."""
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def send_webhook(webhook_url: str, embed: dict, resource_name: str) -> bool:
    payload = {
        "embeds": [embed],
        "username": "Galaxy Harvester",
        "avatar_url": "https://i.imgur.com/UQjAoy2.png",
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code in (200, 204):
            logging.info(f"Announced: {resource_name}")
            return True
        else:
            logging.warning(f"Webhook returned {resp.status_code} for {resource_name}: {resp.text[:200]}")
            return False
    except Exception as e:
        logging.warning(f"Webhook error for {resource_name}: {e}")
        return False


# ──────────────────────────────────────────────
# Main poll loop
# ──────────────────────────────────────────────

def poll(cfg: dict, state: dict) -> dict:
    """
    Fetch the feed, find new resources, announce them, prune stale ones.
    Returns the updated state dict.
    """
    galaxy_id   = cfg["GALAXY_ID"]
    galaxy_name = cfg["GALAXY_NAME"]
    webhook_url = cfg["WEBHOOK_URL"]
    state_file  = cfg["STATE_FILE"]

    xml_text = fetch_feed(cfg["FEED_URL"])
    if not xml_text:
        logging.warning("Could not fetch feed; skipping this cycle.")
        return state

    live_resources = parse_feed_items(xml_text, galaxy_id)
    logging.info(f"Feed contains {len(live_resources)} resource(s) for galaxy {galaxy_id}.")

    # ── Announce new resources ──
    for name, resource in live_resources.items():
        if name not in state:
            logging.info(f"New resource detected: {name} ({resource.get('resource_type','')})")
            if webhook_url:
                embed = build_embed(resource, galaxy_name)
                sent  = send_webhook(webhook_url, embed, name)
                if sent:
                    # Small delay to avoid Discord rate limits
                    time.sleep(1)
            else:
                logging.warning("WEBHOOK_URL not set – skipping Discord notification.")
            state[name] = resource

    # ── Prune resources that left the feed ──
    stale = [n for n in list(state.keys()) if n not in live_resources]
    for name in stale:
        logging.info(f"Resource no longer in feed, removing from state: {name}")
        del state[name]

    save_state(state_file, state)
    return state


def main():
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg["LOG_LEVEL"].upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not cfg["WEBHOOK_URL"]:
        logging.warning(
            "WEBHOOK_URL is not configured. "
            "Set it in config.json or as an environment variable. "
            "The bot will still run but won't post to Discord."
        )

    logging.info(
        f"Galaxy Harvester Bot starting — galaxy_id={cfg['GALAXY_ID']} "
        f"({cfg['GALAXY_NAME']}), poll interval={cfg['POLL_INTERVAL']}s"
    )

    state = load_state(cfg["STATE_FILE"])
    logging.info(f"Loaded {len(state)} resource(s) from saved state.")

    while True:
        try:
            state = poll(cfg, state)
        except Exception as e:
            logging.error(f"Unexpected error in poll cycle: {e}", exc_info=True)

        logging.debug(f"Sleeping {cfg['POLL_INTERVAL']}s…")
        time.sleep(cfg["POLL_INTERVAL"])


if __name__ == "__main__":
    main()
