import os
import sys
import json
import asyncio
import traceback
import signal
import discord
from discord.ext import tasks, commands
from aiohttp import web
import git

# Configuration from environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_IDS = [int(cid.strip()) for cid in os.getenv('CHANNEL_IDS', '').split(',') if cid.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', '360'))
REPO_URL = os.getenv('REPO_URL', 'https://github.com/SimplifyJobs/Summer2027-Internships.git')
LOCAL_REPO_PATH = os.getenv('LOCAL_REPO_PATH', './Summer2027-Internships')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

message_tracking = {}

async def clone_or_update_repo():
    """Clones or updates the repository asynchronously without blocking the event loop."""
    print("Cloning or updating repository...")
    def _sync_clone_or_update():
        if os.path.exists(LOCAL_REPO_PATH):
            try:
                repo = git.Repo(LOCAL_REPO_PATH)
                repo.remotes.origin.pull()
                print("Repository updated.")
            except git.exc.InvalidGitRepositoryError:
                import shutil
                shutil.rmtree(LOCAL_REPO_PATH, ignore_errors=True)
                git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)
                print("Repository cloned fresh.")
        else:
            git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)
            print("Repository cloned fresh.")

    await asyncio.to_thread(_sync_clone_or_update)

def read_json():
    """Reads role data from the cloned JSON file structure."""
    target_file = None
    for root, dirs, files in os.walk(LOCAL_REPO_PATH):
        for file in files:
            if file.endswith('.json') and 'listing' in file.lower():
                target_file = os.path.join(root, file)
                break
        if target_file:
            break

    if not target_file:
        # Look for any json file
        for root, dirs, files in os.walk(LOCAL_REPO_PATH):
            for file in files:
                if file.endswith('.json'):
                    target_file = os.path.join(root, file)
                    break
            if target_file:
                break

    if target_file and os.path.exists(target_file):
        with open(target_file, 'r') as f:
            return json.load(f)
    return []

def format_message(role):
    return f"**New Internship Opportunity!**\n**Company:** {role.get('company_name')}\n**Role:** {role.get('title')}\n[Apply Here]({role.get('url', '#')})"

def format_deactivation_message(role):
    return f"❌ **Expired/Closed:** {role.get('company_name')} - {role.get('title')}"

async def send_messages_to_channels(message, role_key=None):
    for channel_id in CHANNEL_IDS:
