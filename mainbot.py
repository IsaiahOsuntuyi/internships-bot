import os
import sys
import json
import asyncio
import traceback
import signal
from datetime import datetime
import discord
from discord.ext import tasks, commands
from aiohttp import web
import git

# Configuration from environment variables
DISCORD_TOKEN = (os.getenv('DISCORD_TOKEN') or '').strip().strip('"').strip("'")
CHANNEL_IDS = [int(cid.strip()) for cid in os.getenv('CHANNEL_IDS', '').split(',') if cid.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', '360'))
REPO_URL = os.getenv('REPO_URL', 'https://github.com/SimplifyJobs/Summer2027-Internships.git')
LOCAL_REPO_PATH = os.getenv('LOCAL_REPO_PATH', './Summer2027-Internships')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

async def clone_or_update_repo():
    """Clones or updates the repository asynchronously with depth=1 for speed."""
    print("Cloning or updating repository...", flush=True)
    def _sync_clone_or_update():
        if os.path.exists(LOCAL_REPO_PATH):
            try:
                repo = git.Repo(LOCAL_REPO_PATH)
                repo.remotes.origin.pull()
                print("Repository updated.", flush=True)
            except Exception as e:
                print(f"Error updating repo, re-cloning: {e}", flush=True)
                import shutil
                shutil.rmtree(LOCAL_REPO_PATH, ignore_errors=True)
                git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH, depth=1)
                print("Repository cloned after error.", flush=True)
        else:
            git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH, depth=1)
            print("Repository cloned.", flush=True)

    await asyncio.to_thread(_sync_clone_or_update)

def load_listings():
    """Loads listings from the cloned JSON file."""
    for root, dirs, files in os.walk(LOCAL_REPO_PATH):
        for file in files:
            if file.endswith('.json') and 'listing' in file.lower():
                target = os.path.join(root, file)
                with open(target, 'r', encoding='utf-8') as f:
                    return json.load(f)
    print("WARNING: No listing JSON file found!", flush=True)
    return []

def load_previous_data():
    if os.path.exists('previous_data.json'):
        with open('previous_data.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_previous_data(data):
    with open('previous_data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def format_date(timestamp):
    """Converts a standard 10-digit Unix epoch timestamp (in seconds) into a clean readable date string."""
    try:
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).strftime('%B %d, %Y')
        elif isinstance(timestamp, str) and timestamp.isdigit():
            return datetime.fromtimestamp(int(timestamp)).strftime('%B %d, %Y')
        return str(timestamp) if timestamp else "Recently"
    except Exception:
        return "Recently"

async def send_messages_to_channels(embeds):
    for channel_id in CHANNEL_IDS:
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as e:
                print(f"Error fetching channel {channel_id}: {e}", flush=True)
                continue
        if channel:
            for embed in embeds:
                try:
                    await channel.send(embed=embed)
                    print(f"Sent message to channel {channel_id}", flush=True)
                except Exception as e:
                    print(f"Error sending message to channel {channel_id}: {e}", flush=True)

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", 
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", 
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", 
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", 
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}

EXCLUDED_LOCATIONS = [
    "canada", "uk", "united kingdom", "london", "toronto", "vancouver",
    "montreal", "waterloo", "ontario", "british columbia", "alberta", 
    "quebec", "ottawa", "calgary", "india", "germany", "australia", 
    "singapore", "mexico", "brazil", "ireland", "dublin", "poland", 
    "spain", "france", "netherlands", "switzerland", "israel", "china", 
    "japan", "hong kong", "taiwan", "emea", "apac", "latam"
]

def is_us_location(loc: str) -> bool:
    loc_lower = str(loc).lower().strip()
    if any(excl in loc_lower for excl in EXCLUDED_LOCATIONS):
        return False
    tokens = [t.strip() for t in str(loc).replace(",", " ").replace("/", " ").split()]
    if any(token in US_STATES for token in tokens):
        return True
    return any(k in loc_lower for k in ["united states", "usa", "u.s.", "remote"])

def has_us_location(locations) -> bool:
    if not locations:
        return False
    if isinstance(locations, str):
        return is_us_location(locations)
    if isinstance(locations, list):
        return any(is_us_location(loc) for loc in locations)
    return False

async def check_for_new_roles():
    print("Checking for new roles...", flush=True)
    await clone_or_update_repo()
    current_listings = load_listings()
    previous_listings = load_previous_data()

    print(f"Loaded {len(current_listings)} total entries from JSON.", flush=True)

    is_first_run = not previous_listings

    # STRICT RELEVANCE FILTER: Only consider active, visible, US-based roles
    active_listings = [
        item for item in current_listings 
        if item.get('active', True) 
        and item.get('is_visible', True)
        and has_us_location(item.get('locations', []))
    ]

    # SORT BY DATE POSTED: Newest listings first (October 2026 and forward)
    active_listings.sort(key=lambda x: x.get('date_posted', 0) or 0, reverse=True)

    if is_first_run:
        new_roles = active_listings
    else:
        # Use Simplify's unique posting 'id' property to track seen listings and prevent duplicates
        prev_ids = {item.get('id') for item in previous_listings if item.get('id')}
        new_roles = [item for item in active_listings if item.get('id') and item.get('id') not in prev_ids]

    print(f"Found {len(new_roles)} truly active new roles to evaluate.", flush=True)

    # Post up to 10 newest roles per check
    roles_to_post = new_roles[:10] if is_first_run else new_roles[:10]

    if roles_to_post:
        print(f"Posting {len(roles_to_post)} open roles...", flush=True)
        for role in roles_to_post:
            company = role.get('company_name', 'Unknown Company')
            title = role.get('title', 'Internship Role')
            url = role.get('url', '#')
            locations_raw = role.get('locations', ['Remote / Multiple'])
            if isinstance(locations_raw, list):
                us_locs = [loc for loc in locations_raw if is_us_location(loc)]
                locations_str = ", ".join(us_locs) if us_locs else ", ".join(locations_raw)
            else:
                locations_str = str(locations_raw)
            formatted_date = format_date(role.get('date_posted'))    
            # Polished Embed Structure
            embed = discord.Embed(
                title=f"🚀 {company} — {title}",
                url=url,
                color=discord.Color.from_rgb(41, 128, 185) # Professional Blue
            )
            embed.add_field(name="📍 Location", value=locations_str or "N/A", inline=True)
            embed.add_field(name="📅 Date Posted", value=formatted_date, inline=True)
            embed.set_footer(text="Bit Brothers Internships Bot • Powered by Simplify")

            await send_messages_to_channels([embed])
            await asyncio.sleep(1.2)

    save_previous_data(current_listings)
    print("Check completed.", flush=True)

@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def scheduled_check():
    await check_for_new_roles()

@scheduled_check.before_loop
async def before_scheduled_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})", flush=True)
    if not scheduled_check.is_running():
        scheduled_check.start()

@bot.command(name='check')
async def manual_check(ctx):
    await ctx.send("🔍 Running manual internship check...")
    if os.path.exists('previous_data.json'):
        os.remove('previous_data.json')
    await check_for_new_roles()
    await ctx.send("✅ Manual check completed!")

async def handle_health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', '10000'))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server running on port {port}", flush=True)

async def main():
    await start_web_server()
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == '__main__':
    asyncio.run(main())
