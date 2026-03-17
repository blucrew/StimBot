# ⚡🤖 StimBot 3.1

> **The Ultimate 24/7 Discord Audio Experience.**
> StimBot is a bulletproof, premium Discord bot designed to keep the music flowing in your server without interruption. Built with a robust auto-reconnection loop, smart library caching, and interactive UI controls, it guarantees your station never experiences dead air.

---

## ✨ Features

* 🎧 **24/7 Auto-Pilot Playback:** Seamlessly plays a local directory of high-quality `.mp3`, `.wav`, and `.flac` files. If Discord's voice servers hiccup, StimBot automatically catches the drop and reconnects.
* 🎛️ **Interactive Embed UI:** Sleek, dynamic control panels generated directly in your text channel. See what's playing, check time remaining, and control the flow without typing a single command.
* 🗳️ **Democratic Skipping:** Not feeling the current track? Users can vote to skip using the built-in UI buttons. The track only skips when the majority agrees (unless you hold the coveted DJ role, which grants instant bypass power).
* 🎉 **Party Mode:** Elevate the energy. Keyword-activated or slash-command triggered, Party Mode fires off automated, interactive prompts every 4-8 minutes to keep the chat alive.
* 📂 **Smart Library Caching:** Lightning-fast boot times. StimBot uses pickled file caches to index your audio library, meaning zero delays on startup.
* 🛡️ **Admin Alerts & Self-Healing:** Comprehensive health monitoring. If an error occurs, the bot DMs the server administrator with a detailed diagnostic report while actively attempting to heal its own connection loop.

---

## 🚀 Installation & Setup

### 1. Prerequisites
* **Python 3.8+**
* **FFmpeg** (Either installed on your system PATH, or placed directly in the bot's root folder as `ffmpeg.exe`).

### 2. Clone the Repository
```bash
git clone [https://github.com/blucrew/StimBot.git](https://github.com/blucrew/StimBot.git)
cd StimBot
