import json

from pathlib import Path

class SettingsManager:
    def __init__(self, path="settings.json"):
        self.path = Path(__file__).parent / path
        # Init settings data to default values before calling load
        self.data = { "announce_track_changes": False, "announcement_channel_id": 0, "DJ_role_id": 0, "DJ_should_bypass_skip": True, "playback_volume": 0.5, "majority_skip_threshold": 0.51, "audio_file_directory": "." }
        self.load()

    def load(self, output=False):
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    # Merge loaded values over defaults so any missing keys
                    # still fall back to the defaults defined in __init__.
                    self.data = {**self.data, **json.load(f)}

                msg = f"✅ Settings loaded from {self.path}"
            except json.JSONDecodeError:
                msg = f"❌ Invalid JSON in settings file. Previous settings retained."
        else:
            self.save()  # Save current defaults
            msg = f"❌ No settings file found. Defaults saved to {self.path}"

        # Feedback via context or stdout
        if output:
            return msg
        else:
            print(f"[Settings] {msg}")

    def save(self):
        # Write self.data back out to disk.
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)
        print(f"[Settings] Saved to {self.path}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
