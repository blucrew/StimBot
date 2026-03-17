import pickle
import time
import asyncio
import random

from pathlib import Path

class LibraryScanner:
    def __init__(self, settings, cache_file="file_cache.pkl"):
        self.settings = settings
        self.cache_path = Path(__file__).parent / cache_file
        self.allowed_exts = {".mp3", ".wav", ".flac"}
        self.file_cache = []

        # Load or scan
        if self.cache_path.exists():
            self._load_cache()
        else:
            print("[LibraryScanner] Cache not found. Use !mrefresh to scan the active directory.")

    def _get_directory(self):
        raw = self.settings.get("audio_file_directory", ".")
        p = Path(raw)
        if p.is_absolute():
            return p
        # Resolve relative paths against the script's own directory, not the
        # working directory, so the bot works regardless of where it's launched from.
        return (Path(__file__).parent / p).resolve()

    def _load_cache(self):
        try:
            with self.cache_path.open("rb") as f:
                self.file_cache = pickle.load(f)
            print(f"[LibraryScanner] Loaded {len(self.file_cache)} cached files.")
        except Exception as e:
            print(f"[LibraryScanner] Failed to load cache: {e}")
            self.file_cache = []

    def _save_cache(self):
        with self.cache_path.open("wb") as f:
            pickle.dump(self.file_cache, f)

    async def refresh(self, bot=None, interaction=None):
        """Refresh file cache and optionally update a progress message."""
        directory = self._get_directory()
        print(f"[LibraryScanner] Refreshing from: {directory}")

        def scan():
            found = []
            last_update = time.time()
            for f in directory.rglob("*"):
                if f.suffix.lower() in self.allowed_exts:
                    found.append(f.resolve())

                if bot and interaction and time.time() - last_update >= 5:
                    count = len(found)
                    asyncio.run_coroutine_threadsafe(
                        interaction.edit_original_response(content=f"🔎 Scanning... {count} files found."),
                        bot.loop
                    )
                    last_update = time.time()

            return found

        try:
            self.file_cache = await asyncio.to_thread(scan)
            await asyncio.to_thread(self._save_cache)

            if bot and interaction:
                await interaction.edit_original_response(content=f"✅ Scan complete! Indexed {len(self.file_cache)} file(s).")
            else:
                print(f"[LibraryScanner] Scan complete. {len(self.file_cache)} files found.")

        except Exception as e:
            if bot and interaction:
                await interaction.edit_original_response(content=f"❌ Scan failed: `{e}`")
            else:
                print(f"[LibraryScanner] Scan error: {e}")

    def get_random_file(self):
        if not self.file_cache:
            print("[LibraryScanner] No cached files to choose from.")
            return None
        return random.choice(self.file_cache)
