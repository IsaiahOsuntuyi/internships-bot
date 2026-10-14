import os
import sys
import json
import asyncio
import traceback
import discord
from discord.ext import tasks, commands
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
    json_path = os.path.join(LOCAL_REPO_PATH, '.github', 'listings') # Update if path differs
    # Fallback to general search if path structure varies
    target_file = None
    for root, dirs, files in os.walk(LOCAL_REPO_PATH):
        for file in files:
            if file.endswith('.json') and 'listing' in file.lower():
                target_file = os.path.join(root, file)
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
        channel = bot.get_channel(channel_id)
        if channel:
            msg = await channel.send(message)
            if role_key:
                if role_key not in message_tracking:
                    message_tracking[role_key] = []
                message_tracking[role_key].append({'channel_id': channel_id, 'message_id': msg.id, 'timestamp': msg.created_at})

async def update_expired_role_messages(role):
    role_key = f"{role.get('company_name')}__{role.get('title')}"
    if role_key in message_tracking:
        for record in message_tracking[role_key]:
            try:
                channel = bot.get_channel(record['channel_id'])
                if channel:
                    msg = await channel.fetch_message(record['message_id'])
                    await msg.edit(content=f"~~{msg.content}~~ *(Closed)*")
            except Exception as e:
                print(f"Could not edit message: {e}")

async def check_for_new_roles():
    """The function checks for new roles and deactivated roles asynchronously."""
    print("Checking for new roles...")
    await clone_or_update_repo()
    new_data = read_json()
    
    if os.path.exists('previous_data.json'):
        with open('previous_data.json', 'r') as file:
            old_data = json.load(file)
        print("Previous data loaded.")
    else:
        old_data = []
        print("No previous data found.")

    new_roles = []
    deactivated_roles = []
    
    old_roles_dict = {(role.get('title'), role.get('company_name')): role for role in old_data}
    
    for new_role in new_data:
        old_role = old_roles_dict.get((new_role.get('title'), new_role.get('company_name')))
        if old_role:
            if old_role.get('active') and not new_role.get('active'):
                deactivated_roles.append(new_role)
        elif new_role.get('is_visible', True) and new_role.get('active', True):
            new_roles.append(new_role)

    for role in new_roles:
        role_key = f"{role.get('company_name')}_{role.get('title')}"
        message = format_message(role)
        await send_messages_to_channels(message, role_key)

    for role in deactivated_roles:
        await update_expired_role_messages(role)
        message = format_deactivation_message(role)
        await send_messages_to_channels(message)

    with open('previous_data.json', 'w') as file:
        json.dump(new_data, file)
    print("Updated previous data with new data.")

@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def check_roles_task():
    try:
        await check_for_new_roles()
    except Exception as e:
        print(f"Error during scheduled role check: {e}")
        traceback.print_exc()

@check_roles_task.before_loop
async def before_check_roles_task():
    """Wait until the bot is connected before the first check runs."""
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    """Event handler for when the bot is ready and connected to Discord."""
    print(f'Logged in as {bot.user}')
    print(f'Bot is ready and monitoring {len(CHANNEL_IDS)} channels')
    if not check_roles_task.is_running():
        check_roles_task.start()

# Graceful shutdown handler
import signal
def signal_handler(sig, frame):
    print("\nShutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN environment variable not set.")
        sys.exit(1)
    bot.run(DISCORD_TOKEN)

if __name__ == '__main__':
    main()
