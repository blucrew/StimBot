import os
import sys
import asyncio
import discord
import time
import shutil
import random
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from discord.ext import commands
from dotenv import load_dotenv

# --- Logging setup -----------------------------------------------------------
# Mirror everything to stimbot.log (rotating, 5MB x 5) AND stdout so we can
# diagnose crashes after a Ctrl+C. Replaces prior bare print() calls via a
# module-level logger; existing print() calls are left in place and captured
# through a stdout redirect below so nothing is lost.
LOG_PATH = Path(__file__).parent / "stimbot.log"
_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
log = logging.getLogger("stimbot")

# Route stray print() output (from AudioPlayer etc.) through the logger too,
# so the log file captures everything without touching every call site.
class _PrintToLog:
    def __init__(self, level=logging.INFO):
        self._level = level
        self._buf = ""
    def write(self, msg):
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                log.log(self._level, line.rstrip())
    def flush(self):
        if self._buf.strip():
            log.log(self._level, self._buf.rstrip())
        self._buf = ""
sys.stdout = _PrintToLog(logging.INFO)
sys.stderr = _PrintToLog(logging.ERROR)
# -----------------------------------------------------------------------------

from AudioPlayer import AudioPlayer
from SettingsManager import SettingsManager
from LibraryScanner import LibraryScanner

# Load secrets from the .env file
load_dotenv()
DISCORD_BOT_KEY = os.getenv("DISCORD_BOT_KEY")

if not DISCORD_BOT_KEY:
    raise ValueError("🚨 DISCORD_BOT_KEY is missing! Check your .env file.")

ALLOWED_VOICE_CHANNEL_ID = 1238177610102472724  # Auto Driving channel ID
ADMIN_USER_ID = 159290405744017409  # Your Discord user ID for DM alerts
STIMSTATION_ENGINEERS_ROLE_ID = 829065558233579571  # StimStation Engineers role
LOGS_CHANNEL_ID = 1415434598258708500  # Logs channel ID

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(intents=intents, command_prefix=".")

settings = SettingsManager()
scanner = LibraryScanner(settings)


FFMPEG_PATH = Path(__file__).parent / 'ffmpeg.exe'

def check_ffmpeg():
    """Check if FFmpeg is available (local binary takes priority over PATH)"""
    if FFMPEG_PATH.exists():
        return  # Local ffmpeg.exe found
    if not shutil.which('ffmpeg'):
        raise RuntimeError("FFmpeg is not installed or not in PATH, and ffmpeg.exe was not found in the bot directory.")


# Alert deduplication: suppress identical (severity, message) alerts within
# ALERT_DEDUPE_WINDOW seconds. Prevents the 5-min self-check from DM-spamming
# the admin with the same warning forever.
ALERT_DEDUPE_WINDOW = 1800  # 30 minutes
_recent_alerts: dict[tuple, float] = {}

async def send_admin_alert(message, severity="INFO"):
    """Send alert to admin via DM (deduped within ALERT_DEDUPE_WINDOW)"""
    key = (severity, message)
    now = time.time()
    last = _recent_alerts.get(key)
    if last is not None and (now - last) < ALERT_DEDUPE_WINDOW:
        log.debug(f"[Alert] Suppressed duplicate {severity} alert: {message}")
        return
    _recent_alerts[key] = now
    # Cheap GC so the dict doesn't grow forever
    if len(_recent_alerts) > 200:
        for k, t in list(_recent_alerts.items()):
            if now - t > ALERT_DEDUPE_WINDOW:
                _recent_alerts.pop(k, None)
    try:
        admin_user = bot.get_user(ADMIN_USER_ID)
        if admin_user:
            severity_emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
            embed = discord.Embed(
                title=f"{severity_emoji.get(severity, 'ℹ️')} StimBot Alert - {severity}",
                description=message,
                color=discord.Color.blue() if severity == "INFO" else discord.Color.yellow() if severity == "WARNING" else discord.Color.red() if severity == "ERROR" else discord.Color.dark_red(),
                timestamp=discord.utils.utcnow()
            )
            await admin_user.send(embed=embed)
            print(f"[Alert] Sent {severity} alert to admin: {message}")
        else:
            print(f"[Alert] Could not find admin user to send alert: {message}")
    except Exception as e:
        print(f"[Alert] Failed to send admin alert: {e}")

async def log_user_interaction(interaction_type: str, user: discord.User, details: str = "", success: bool = True):
    """Log user interactions to the logs channel"""
    try:
        logs_channel = bot.get_channel(LOGS_CHANNEL_ID)
        if not logs_channel:
            print(f"[Logging] Could not find logs channel with ID {LOGS_CHANNEL_ID}")
            return
        
        emoji_map = {"slash_command": "⚡", "button_click": "🔘", "bot_action": "🤖", "error": "❌"}
        color_map = {"slash_command": discord.Color.blue(), "button_click": discord.Color.green(), "bot_action": discord.Color.blurple(), "error": discord.Color.red()}
        
        emoji = emoji_map.get(interaction_type, "📝")
        color = color_map.get(interaction_type, discord.Color.light_grey())
        
        if not success:
            emoji = "❌"
            color = discord.Color.red()
        
        embed = discord.Embed(
            title=f"{emoji} {interaction_type.replace('_', ' ').title()}",
            color=color,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{user.display_name} ({user.mention})", inline=True)
        embed.add_field(name="User ID", value=str(user.id), inline=True)
        embed.add_field(name="Success", value="✅" if success else "❌", inline=True)
        
        if details:
            embed.add_field(name="Details", value=details, inline=False)
        
        if hasattr(user, 'roles'):
            special_roles = [role.name for role in user.roles if role.id in [STIMSTATION_ENGINEERS_ROLE_ID]]
            if special_roles:
                embed.add_field(name="Special Roles", value=", ".join(special_roles), inline=False)
        
        await logs_channel.send(embed=embed)
        
    except Exception as e:
        print(f"[Logging] Error logging user interaction: {e}")

async def run_self_checks():
    """Run comprehensive self-checks (self-heals where possible before alerting)"""
    issues = []
    try:
        if not FFMPEG_PATH.exists() and not shutil.which('ffmpeg'):
            issues.append("❌ FFmpeg not found in PATH")

        # Only warn about "not connected" if the autoplay loop ISN'T already
        # actively reconnecting — otherwise this fires every 5 min during a
        # healthy healing window and spams the admin.
        if player.voice_client and not player.voice_client.is_connected() and not getattr(player, "reconnecting", False):
            issues.append("⚠️ Voice client exists but not connected")

        if not scanner.file_cache:
            issues.append("⚠️ No music files in cache - library may need refresh")

        if not settings.get('audio_file_directory') or not Path(settings.get('audio_file_directory', '.')).exists():
            issues.append("❌ Audio file directory not configured or does not exist")

        # Self-heal missing embed instead of just alerting. This gets wiped to
        # None whenever update_embed() hits NotFound/HTTPException (e.g. admin
        # deleted the message, channel purge, transient HTTP error), and prior
        # behavior was to DM every 5 min forever without fixing it.
        if player.voice_client and player.voice_client.is_connected() and not player.embed_message:
            healed = False
            try:
                target_channel = player.embed_channel or player.voice_client.channel
                if target_channel:
                    effective_view = player.view_class or MusicControlView
                    player.embed_message = await target_channel.send(
                        embed=player.create_embed(), view=effective_view()
                    )
                    player.embed_channel = target_channel
                    log.info("[SelfCheck] Self-healed missing embed message")
                    healed = True
            except Exception as e:
                log.warning(f"[SelfCheck] Embed self-heal failed: {e}")
            if not healed:
                issues.append("⚠️ Voice client active but no embed message present (self-heal failed)")

        if player.voice_client and player.voice_client.is_connected() and not player.loop_task:
            issues.append("🚨 CRITICAL: Voice client connected but autoplay loop not running! (24/7 failure)")

        if issues:
            issue_report = "\n".join(issues)
            severity = "CRITICAL" if any("🚨" in issue or "❌" in issue for issue in issues) else "WARNING"
            await send_admin_alert(f"Self-check found issues:\n\n{issue_report}", severity)
        else:
            print("[SelfCheck] All systems normal")
            
    except Exception as e:
        await send_admin_alert(f"Self-check system error: {str(e)}", "ERROR")

async def start_health_monitor():
    """Start periodic health monitoring"""
    while True:
        try:
            await asyncio.sleep(300)
            await run_self_checks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            await send_admin_alert(f"Health monitor error: {str(e)}", "ERROR")
            await asyncio.sleep(60)

def check_permissions(interaction: discord.Interaction) -> bool:
    """Check if user has permission to use bot commands"""
    return interaction.user.id == ADMIN_USER_ID or STIMSTATION_ENGINEERS_ROLE_ID in [role.id for role in interaction.user.roles]

player = AudioPlayer(settings, scanner, bot)
last_public_info = 0
INFO_COOLDOWN = 900
# --- Poppers system disabled (handled by separate bot) ---
# poppers_task = None
# poppers_party_mode = False
# poppers_party_end_time = 0
#
# async def send_poppers_prompt():
#     """Send a random poppers prompt to the voice channel's text channel"""
#     try:
#         if not player.voice_client or not player.voice_client.is_connected(): return
#         if not any(not m.bot for m in player.voice_client.channel.members): return
#
#         text_channel = player.voice_client.channel
#         prompts = [("Hit Em", 1), ("Double hit", 2), ("deep huff", 1)]
#         prompt_text, emoji_count = random.choice(prompts)
#         emoji_string = "<:pp:1405461735539740702>" * emoji_count
#
#         embed = discord.Embed(
#             title="Party Poppers Time!",
#             description=f"**{prompt_text}** {emoji_string}",
#             color=0xFFD700
#         ).set_footer(text="Get the party started!")
#
#         message = await text_channel.send(embed=embed)
#         await asyncio.sleep(60)
#         await message.delete()
#
#     except Exception as e:
#         print(f"[Poppers] CRITICAL ERROR in send_poppers_prompt: {e}")
#         await send_admin_alert(f"Poppers system error: {str(e)}", "ERROR")
#
# async def poppers_prompt_loop():
#     """Main loop for sending poppers prompts - every 4-8 minutes"""
#     global poppers_party_mode, poppers_party_end_time
#     try:
#         while True:
#             if poppers_party_mode and time.time() > poppers_party_end_time:
#                 poppers_party_mode = False
#                 print("[Poppers] Party mode ended")
#
#             next_prompt = random.randint(240, 480)  # 4-8 minutes
#             await asyncio.sleep(next_prompt)
#             await send_poppers_prompt()
#
#     except asyncio.CancelledError:
#         print("[Poppers] Prompt loop cancelled")
#     except Exception as e:
#         print(f"[Poppers] Error in prompt loop: {e}")
#
# def start_poppers_prompts():
#     global poppers_task
#     if not poppers_task or poppers_task.done():
#         poppers_task = asyncio.create_task(poppers_prompt_loop())
#         print("[Poppers] Prompt system started.")
#
# def stop_poppers_prompts():
#     global poppers_task
#     if poppers_task and not poppers_task.done():
#         poppers_task.cancel()
#         poppers_task = None
#         print("[Poppers] Prompt system stopped.")

class MusicControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Info', style=discord.ButtonStyle.secondary, emoji='ℹ️', custom_id='music_info')
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            global last_public_info
            current_time = time.time()
            await log_user_interaction("button_click", interaction.user, "Info button clicked")
            
            if player.get_now_playing() and player.voice_client and player.voice_client.is_connected():
                info_msg = (
                    f"🎵 **Currently Playing:** {player.get_now_playing()}\n"
                    f"⏰ **Time Remaining:** {player.get_remaining_time()}\n"
                    f"🔊 **Channel:** {player.voice_client.channel.name}\n"
                    f"👥 **Listeners:** {len([m for m in player.voice_client.channel.members if not m.bot])}\n\n"
                    f"🔥 **24/7 Mode:** Active - music never stops!"
                )
            else:
                info_msg = "💤 **Status:** Nothing currently playing\n🔥 **24/7 Mode:** Contact StimStation Engineers if music stops!"
            
            if current_time - last_public_info >= INFO_COOLDOWN:
                await interaction.response.send_message(f"📢 **StimBot Status Update**\n\n{info_msg}", ephemeral=False)
                last_public_info = current_time
            else:
                minutes_remaining = int((INFO_COOLDOWN - (current_time - last_public_info)) // 60)
                await interaction.response.send_message(f"{info_msg}\n\n🕐 *Public info available in {minutes_remaining} minutes to prevent spam*", ephemeral=True)
        except Exception as e:
            print(f"[MusicControlView] Error in info_button: {e}")
            await log_user_interaction("button_click", interaction.user, f"Info button error: {str(e)}", False)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error getting bot info.", ephemeral=True)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.green, emoji='⭐', custom_id='music_next')
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await log_user_interaction("button_click", interaction.user, "Next/Skip button clicked")
            if settings.get('DJ_role_id') in [role.id for role in interaction.user.roles] and settings.get('DJ_should_bypass_skip'):
                await player.skip_track_from_button(interaction.user, interaction)
            else:
                await player.vote_skip_from_button(interaction.user, interaction)
        except Exception as e:
            print(f"[MusicControlView] Error in next_button: {e}")
            await log_user_interaction("button_click", interaction.user, f"Next button error: {str(e)}", False)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error processing skip request.", ephemeral=True)

# async def activate_poppers_party(channel: discord.TextChannel, user: discord.User):
#     """Activates poppers party mode, sends announcements, and alerts admin."""
#     global poppers_party_mode, poppers_party_end_time
#     stop_poppers_prompts()
#     poppers_party_mode = True
#     poppers_party_end_time = time.time() + 600
#     start_poppers_prompts()
#     embed = discord.Embed(
#         title="POPPERS PARTY MODE ACTIVATED!",
#         description="Prompts every 4-8 minutes for the next 10 minutes!\n\n<:pp:1405461735539740702> Get ready to party! <:pp:1405461735539740702>",
#         color=0xFF4500
#     ).set_footer(text="Party mode will end automatically in 10 minutes")
#     await channel.send(embed=embed)
#     await send_poppers_prompt()
#     print(f"[Poppers] Party mode activated by {user.display_name}")
#
# @bot.tree.command(name="poppersparty", description="Activate party mode - rapid poppers prompts for 10 minutes")
# async def poppersparty(interaction: discord.Interaction):
#     await log_user_interaction("slash_command", interaction.user, "poppersparty command")
#     if not check_permissions(interaction):
#         return await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
#     if poppers_party_mode:
#         return await interaction.response.send_message("Party mode is already active!", ephemeral=True)
#     await interaction.response.defer(ephemeral=True)
#     await activate_poppers_party(interaction.channel, interaction.user)
#     await interaction.followup.send("Party mode activated!", ephemeral=True)


@bot.tree.command(name="play", description="Start 24/7 music playback in Auto Driving")
async def play(interaction: discord.Interaction):
    await log_user_interaction("slash_command", interaction.user, "play command")
    if not check_permissions(interaction):
        return await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    if not interaction.user.voice or interaction.user.voice.channel.id != ALLOWED_VOICE_CHANNEL_ID:
        return await interaction.response.send_message("Please join the Auto Driving voice channel first.", ephemeral=True)
    if player.get_now_playing():
        return await interaction.response.send_message("Playback is already active.", ephemeral=True)

    await player.connect(interaction.user.voice.channel, interaction.guild.voice_client, MusicControlView)
    if bot.voice_clients:
        player.start_loop()
        await interaction.response.send_message("Playback started.", ephemeral=True)
    else:
        await interaction.response.send_message("Error connecting to the voice channel.", ephemeral=True)

@bot.tree.command(name="stop", description="Stop 24/7 music playback")
async def stop(interaction: discord.Interaction):
    await log_user_interaction("slash_command", interaction.user, "stop command")
    if not check_permissions(interaction):
        return await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    await player.stop_loop()
    await player.update_announcement()
    await interaction.response.send_message("Playback stopped.", ephemeral=True)

@bot.tree.command(name="disconnect", description="Disconnect bot from voice channel")
async def disconnect(interaction: discord.Interaction):
    await log_user_interaction("slash_command", interaction.user, "disconnect command")
    if not check_permissions(interaction):
        return await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    await player.disconnect()
    await interaction.response.send_message("Disconnected from voice channel.", ephemeral=True)

@bot.tree.command(name="refresh", description="Re-scan music library for new files")
async def refresh(interaction: discord.Interaction):
    await log_user_interaction("slash_command", interaction.user, "refresh command")
    if not check_permissions(interaction):
        return await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    await interaction.response.send_message("Starting library scan...", ephemeral=True)
    await scanner.refresh(bot, interaction)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    # --- Poppers keyword detection disabled (handled by separate bot) ---
    # global poppers_party_mode
    # if not player.voice_client or not player.voice_client.is_connected(): return
    # if message.channel.id != player.voice_client.channel.id: return
    # if not poppers_party_mode:
    #     keywords = ['poppers', 'popper', 'pp', 'huff']
    #     message_content_lower = message.content.lower()
    #     if any(word in message_content_lower for word in keywords):
    #         await activate_poppers_party(message.channel, message.author)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(f"[Startup] {e}")
        await send_admin_alert(f"🚨 CRITICAL: {e}", "CRITICAL")
        return await bot.close()

    bot.add_view(MusicControlView())
    await scanner.refresh()

    # Brief pause to let the gateway fully settle before attempting voice
    await asyncio.sleep(3)

    try:
        auto_driving_channel = bot.get_channel(ALLOWED_VOICE_CHANNEL_ID)
        if auto_driving_channel:
            print(f"[Startup] Attempting to join {auto_driving_channel.name}")
            await player.connect(auto_driving_channel, None, MusicControlView)
            if player.voice_client and player.voice_client.is_connected():
                print(f"[Startup] Successfully joined {auto_driving_channel.name}")
                await player.update_announcement()
            else:
                print("[Startup] Initial voice connection failed - autoplay loop will retry")
                await send_admin_alert("⚠️ Failed to connect to voice on startup. Autoplay loop will retry.", "WARNING")
        else:
            print(f"[Startup] Could not find voice channel ID {ALLOWED_VOICE_CHANNEL_ID}")
            await send_admin_alert(f"⚠️ Could not find Auto Driving channel on startup. Autoplay loop will retry.", "WARNING")
    except Exception as e:
        print(f"[Startup] Voice connection error: {e}")
        await send_admin_alert(f"⚠️ Startup voice error: {str(e)}. Autoplay loop will retry.", "WARNING")

    player.start_loop()
    # start_poppers_prompts()  # Disabled - handled by separate bot
    asyncio.create_task(start_health_monitor())
    await send_admin_alert(f"✅ StimBot started as {bot.user}", "INFO")
    await run_self_checks()


@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user and after.channel and after.channel.id != ALLOWED_VOICE_CHANNEL_ID:
        print(f"[Bot] Detected move to unauthorized channel: {after.channel.name}. Moving back.")
        await send_admin_alert(f"🚨 Bot was moved to unauthorized channel: {after.channel.name}.", "WARNING")
        try:
            allowed_channel = bot.get_channel(ALLOWED_VOICE_CHANNEL_ID)
            if allowed_channel and player.voice_client:
                await player.voice_client.move_to(allowed_channel)
        except Exception as e:
            await send_admin_alert(f"❌ Failed to move back to Auto Driving channel: {str(e)}", "ERROR")
        return

    if member == bot.user and before.channel and not after.channel:
        print("[Bot] Detected disconnection from voice channel. Autoplay loop will handle reconnection.")
        return

    if member.bot: return
    vc = player.voice_client
    if not vc or not vc.is_connected(): return
    
    if before.channel == vc.channel and after.channel != vc.channel:
        player.remove_from_vote_skip(member.id)

@bot.command()
@commands.is_owner()
async def sync(ctx: commands.Context):
    """Manually syncs slash commands."""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} slash commands globally.")
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        await ctx.send(f"⚠️ Failed to sync commands: {e}")
        print(f"Failed to sync commands: {e}")

bot.run(DISCORD_BOT_KEY)