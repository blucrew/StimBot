import math
import random
import asyncio
import discord
import time
import mutagen

from pathlib import Path

# Use local ffmpeg.exe if present, otherwise fall back to system PATH
_LOCAL_FFMPEG = Path(__file__).parent / 'ffmpeg.exe'
FFMPEG_EXECUTABLE = str(_LOCAL_FFMPEG) if _LOCAL_FFMPEG.exists() else 'ffmpeg'

class AudioPlayer:
    def __init__(self, settings, scanner, bot):
        self.now_playing = None
        self.skip_votes = []
        self.loop_task = None
        self.voice_client = None
        self.settings = settings
        self.scanner = scanner
        self.bot = bot
        self.embed_message = None
        self.embed_channel = None
        self.track_start_time = None
        self.track_duration = None
        self.empty_channel_task = None
        self.view_class = None  # Stored so reconnection can restore the control buttons
        self.allowed_channel_id = 1238177610102472724
        self.admin_user_id = 159290405744017409
        self.announcement_channel_id = 964970259104825394 # 🎵 New dedicated channel for 'Now Playing' messages

    async def send_alert(self, message, severity="INFO"):
        try:
            admin_user = self.bot.get_user(self.admin_user_id)
            if admin_user:
                severity_emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
                embed = discord.Embed(
                    title=f"{severity_emoji.get(severity, 'ℹ️')} AudioPlayer Alert - {severity}",
                    description=message,
                    color=discord.Color.blue() if severity == "INFO" else discord.Color.yellow() if severity == "WARNING" else discord.Color.red() if severity == "ERROR" else discord.Color.dark_red(),
                    timestamp=discord.utils.utcnow()
                )
                await admin_user.send(embed=embed)
        except Exception as e:
            print(f"[AudioPlayer Alert] Failed to send alert: {e}")

    # --- MODIFIED: Sends announcements to the dedicated channel ---
    async def announce(self, msg):
        try:
            announce_channel = self.bot.get_channel(self.announcement_channel_id)
            if announce_channel:
                await announce_channel.send(msg)
            else:
                print(f"[AudioPlayer] Could not find announcement channel with ID {self.announcement_channel_id}")
        except Exception as e:
            print(f"[AudioPlayer] Error in announce: {e}")

    def get_track_duration(self, file_path):
        try:
            return mutagen.File(file_path).info.length
        except Exception:
            return None

    def get_remaining_time(self):
        if not self.track_start_time or not self.track_duration: return "Unknown"
        remaining = max(0, self.track_duration - (time.time() - self.track_start_time))
        return f"{int(remaining // 60)}:{int(remaining % 60):02d}"

    def create_embed(self):
        embed = discord.Embed(title="⚡🤖Stimbot 3.1", color=discord.Color.red() if self.skip_votes else discord.Color.gold())
        if self.now_playing:
            embed.add_field(name="⚡ Now Playing", value=self.now_playing.stem, inline=False)
            embed.add_field(name="⏰ Time Remaining", value=self.get_remaining_time(), inline=False)
        else:
            embed.add_field(name="💤 Status", value="Nothing currently playing", inline=False)
        
        if self.skip_votes and self.voice_client:
            total_users = len([m for m in self.voice_client.channel.members if not m.bot])
            needed = math.ceil(total_users * self.settings.get("majority_skip_threshold", 0.5))
            last_voter = self.bot.get_user(self.skip_votes[-1])
            embed.add_field(name="⏭ Skip Vote", value=f"{last_voter.display_name if last_voter else 'Someone'} voted to skip ({len(self.skip_votes)}/{needed} needed)", inline=False)
        
        return embed

    async def update_embed(self):
        if not self.embed_message: return
        try:
            await self.embed_message.edit(embed=self.create_embed())
        except (discord.NotFound, discord.HTTPException):
            self.embed_message = None
            self.embed_channel = None

    async def connect(self, voice_channel, vc, view_class=None, text_channel=None):
        try:
            text_channel = text_channel or voice_channel
            if view_class:
                self.view_class = view_class  # Remember for reconnections
            if vc and vc.is_connected():
                await vc.move_to(voice_channel)
                self.voice_client = vc
            else:
                # reconnect=False disables discord.py's internal retry loop so our
                # own reconnection logic is the single source of retry timing.
                self.voice_client = await voice_channel.connect(reconnect=False)

            if self.embed_message:
                try: await self.embed_message.delete()
                except: pass

            embed = self.create_embed()
            effective_view = (self.view_class or view_class)
            view = effective_view() if effective_view else None
            self.embed_message = await text_channel.send(embed=embed, view=view)
            self.embed_channel = text_channel
        except Exception as e:
            print(f"[AudioPlayer] Error connecting: {e}")

    async def disconnect(self):
        if self.loop_task: self.loop_task.cancel()
        if self.voice_client: await self.voice_client.disconnect()
        if self.embed_message:
            try: await self.embed_message.delete()
            except: pass
        self.voice_client = self.embed_message = self.embed_channel = self.loop_task = None

    # --- MODIFIED: Restore the call to self.announce ---
    async def play_file(self, path: Path):
        if not self.voice_client or not self.voice_client.is_connected():
            print(f"[AudioPlayer] play_file: voice client not ready")
            return
        if not path.exists():
            print(f"[AudioPlayer] play_file: file not found on disk: {path}")
            return
        self.now_playing = path
        self.skip_votes = []
        self.track_start_time = time.time()
        self.track_duration = self.get_track_duration(path)
        
        await self.announce(f"⚡ Now playing: **{self.now_playing.stem}**")
        
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(str(path), executable=FFMPEG_EXECUTABLE), volume=self.settings.get("playback_volume"))
        self.voice_client.play(source)
        await self.update_embed()

    async def play_random(self):
        if random_file := self.scanner.get_random_file():
            await self.play_file(random_file)
        else:
            await self.send_alert("⚠️ Library is empty - no files to play!", "WARNING")

    def start_loop(self):
        if not self.loop_task:
            self.loop_task = asyncio.create_task(self._autoplay_loop())

    async def stop_loop(self):
        if self.loop_task:
            self.loop_task.cancel()
            self.loop_task = None
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
        self.now_playing = self.track_start_time = self.track_duration = None
        self.skip_votes = []
        await self.update_embed()

    async def _autoplay_loop(self):
        try:
            loop_start_time = time.time()
            tracks_played = 0
            
            while True:
                if not self.voice_client or not self.voice_client.is_connected():
                    print("[AudioPlayer] Autoplay loop detected disconnection. Waiting before reconnection...")
                    reconnect_start_time = time.time()
                    reconnected = False
                    retry_delay = 30  # Start at 30s to let discord.py's own retry logic settle first

                    while time.time() - reconnect_start_time < 600:  # 10-minute window
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 120)  # Exponential backoff, cap at 2 min

                        # Re-check — discord.py may have already reconnected on its own
                        if self.voice_client and self.voice_client.is_connected():
                            print("[AudioPlayer] Voice client recovered on its own.")
                            reconnected = True
                            break

                        try:
                            if self.voice_client:
                                try:
                                    await self.voice_client.disconnect()
                                except Exception:
                                    pass
                            self.voice_client = None

                            allowed_channel = self.bot.get_channel(self.allowed_channel_id)
                            if not allowed_channel:
                                await self.send_alert("❌ Could not find Auto Driving channel for reconnection. Loop stopping.", "CRITICAL")
                                return

                            await self.connect(allowed_channel, None, None, self.embed_channel or allowed_channel)
                            if self.voice_client and self.voice_client.is_connected():
                                print("[AudioPlayer] Successfully reconnected to voice channel.")
                                reconnected = True
                                break
                            else:
                                print(f"[AudioPlayer] Reconnection attempt failed, retrying in {retry_delay}s...")
                        except Exception as e:
                            print(f"[AudioPlayer] Reconnection attempt error: {e}")

                    if not reconnected:
                        await self.send_alert("🚨 CRITICAL: Failed to reconnect to voice channel after 10 minutes. Manual intervention required.", "CRITICAL")
                        return

                try:
                    await self.play_random()
                    tracks_played += 1
                except Exception as e:
                    await self.send_alert(f"❌ Error during track playback: {str(e)}", "ERROR")
                    await asyncio.sleep(5)
                    continue

                await asyncio.sleep(3)
                
                if not self.voice_client or not self.voice_client.is_playing():
                    print(f"[AudioPlayer] WARN: Track failed to start playing. Last file: {self.now_playing}")
                    await asyncio.sleep(5)  # Prevent tight loop on repeated failures
                    continue

                # Fall back to 4 hours if mutagen can't read the duration.
                # SundayDriveLIVE episodes can be 1-3 hours; 300s was too short.
                track_timeout = time.time() + (self.track_duration or 14400) + 60
                last_embed_update = time.time()
                
                while self.voice_client and self.voice_client.is_playing():
                    await asyncio.sleep(5)
                    if time.time() - last_embed_update >= 30:
                        await self.update_embed()
                        last_embed_update = time.time()
                    
                    if time.time() > track_timeout:
                        print(f"[AudioPlayer] WARN: Track timed out, forcing skip: {self.now_playing.stem if self.now_playing else 'Unknown'}")
                        if self.voice_client: self.voice_client.stop()
                        break
                
                await asyncio.sleep(1)
                
                if tracks_played > 0 and tracks_played % 25 == 0:
                    runtime = (time.time() - loop_start_time) / 3600
                    if runtime > 0.01:
                        await self.send_alert(f"ℹ️ 24/7 Milestone: {tracks_played} tracks in {runtime:.1f}h", "INFO")
                    
        except asyncio.CancelledError:
            print(f"[AudioPlayer] Autoplay loop cancelled after {tracks_played} tracks")
        except Exception as e:
            await self.send_alert(f"🚨 CRITICAL: Autoplay loop crashed: {str(e)}", "CRITICAL")
        finally:
            # Only clear loop_task if we are still the current task.
            # If start_loop() already replaced it with a new task, don't wipe it.
            if self.loop_task is asyncio.current_task():
                self.loop_task = None

    async def vote_skip_from_button(self, user, interaction):
        if not self.voice_client or not self.voice_client.is_playing() or not user.voice or user.voice.channel != self.voice_client.channel:
            return await interaction.response.send_message("🚫 You must be in the voice channel to vote.", ephemeral=True)
        if user.id in self.skip_votes:
            return await interaction.response.send_message("💤 You have already voted.", ephemeral=True)

        self.skip_votes.append(user.id)
        total_users = len([m for m in self.voice_client.channel.members if not m.bot])
        needed = math.ceil(total_users * self.settings.get("majority_skip_threshold", 0.5))
        
        await interaction.response.send_message(f"You've voted to skip ({len(self.skip_votes)}/{needed}).", ephemeral=True)
        await self.update_embed()

        if len(self.skip_votes) >= needed:
            await self.skip_track_from_button(user, interaction, already_responded=True)

    def remove_from_vote_skip(self, user_id):
        if user_id in self.skip_votes:
            self.skip_votes.remove(user_id)
            asyncio.create_task(self.update_embed())

    async def skip_track_from_button(self, user, interaction, already_responded=False):
        if not self.voice_client or not self.voice_client.is_playing(): return
        await self.stop_loop()
        self.start_loop()
        # The main announce call is now in play_file, so this one can be removed to avoid duplication
        # await self.announce("✅ Track skipped.") 
        if not already_responded:
            await interaction.response.send_message("✅ Track skipped.", ephemeral=True)

    def get_now_playing(self):
        return self.now_playing.stem if self.now_playing else None