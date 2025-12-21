import os
import re
import asyncio
import gc
from threading import Thread
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeAnimated
)
from telethon.tl.functions.messages import ImportChatInviteRequest
from PIL import Image
from flask import Flask

# ================= ENV =================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]
TARGET_CHANNEL = int(os.environ["TARGET_CHANNEL"])
CHANNEL_INVITE = os.environ["CHANNEL_INVITE"]
PORT = int(os.environ.get("PORT", 10000))

# ================= PATHS =================
TMP = "/tmp/work"
os.makedirs(TMP, exist_ok=True)

thumb_src = os.path.join(TMP, "thumb_src.jpg")
thumb_final = os.path.join(TMP, "thumb.jpg")

current_thumb = None
rename_template = None

# ================= CLIENT =================
client = TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH)

# ================= THUMB =================
def optimize_thumbnail(src, dst):
    img = Image.open(src).convert("RGB")
    w, h = img.size

    if max(w, h) > 320:
        ratio = 320 / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    for q in range(90, 30, -5):
        img.save(dst, "JPEG", quality=q)
        if os.path.getsize(dst) <= 200 * 1024:
            return

    img.save(dst, "JPEG", quality=25)

# ================= HELPERS =================
def extract_episode(text, file_name):
    patterns = [
        r"[Ee][Pp][\s\-_:]*([0-9]+)",
        r"[Ee][\s\-_:]*([0-9]+)",
        r"episode[\s\-_:]*([0-9]+)",
        r"ep([0-9]+)",
        r"e([0-9]+)"
    ]
    check_text = (text or "") + " " + (file_name or "")
    for pat in patterns:
        m = re.search(pat, check_text)
        if m:
            return m.group(1)
    return None

def clean_filename(name):
    return re.sub(r'[^\w\-. ]', '_', name)

# ================= SINGLE PROCESS LOCK =================
processing_lock = asyncio.Lock()

# ================= HANDLER =================
@client.on(events.NewMessage)
async def handler(event):
    global current_thumb, rename_template

    msg = event.message

    if not event.is_private:
        return
    if msg.peer_id.user_id != (await client.get_me()).id:
        return

    if msg.raw_text.startswith("/rename"):
        parts = msg.raw_text.split(" ", 1)
        if len(parts) == 1 or parts[1].lower() == "none":
            rename_template = None
            await event.reply("🟦 Rename OFF.")
            return
        rename_template = parts[1].strip()
        await event.reply(f"🟩 Rename template set:\n`{rename_template}`")
        return

    if msg.photo:
        src = await msg.download_media(file=thumb_src)
        optimize_thumbnail(src, thumb_final)
        current_thumb = thumb_final
        await event.reply("✅ Thumbnail saved.")
        return

    is_video = False
    video_duration = 1
    file_name_original = msg.file.name if msg.file else ""

    if msg.video:
        is_video = True
        try:
            video_duration = msg.video.attributes[0].duration
        except:
            pass

    if msg.document:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                is_video = True
                video_duration = attr.duration

    if not is_video or not current_thumb:
        return

    async with processing_lock:
        await event.reply("⬇ Downloading video…")
        video_path = await msg.download_media(file=os.path.join(TMP, f"video_{msg.id}"))

        final_name = None
        if rename_template:
            ep = extract_episode(msg.text or "", file_name_original)
            final_name = rename_template.replace("{ep}", ep or "")
            if not final_name.lower().endswith(".mp4"):
                final_name += ".mp4"
            final_name = clean_filename(final_name)
            new_path = os.path.join(TMP, final_name)
            os.rename(video_path, new_path)
            video_path = new_path

        await event.reply("⬆ Uploading…")

        try:
            await client.send_file(
                TARGET_CHANNEL,
                video_path,
                caption=msg.text or "",
                thumb=current_thumb,
                attributes=[DocumentAttributeVideo(
                    duration=video_duration,
                    w=1280,
                    h=720,
                    supports_streaming=True
                )],
                part_size_kb=256
            )
            await event.reply("✔ Uploaded.")
        finally:
            if os.path.exists(video_path):
                os.remove(video_path)
            gc.collect()

# ================= MAIN =================
async def main():
    await client.start()
    try:
        await client(ImportChatInviteRequest(CHANNEL_INVITE))
    except:
        pass
    await client.run_until_disconnected()

# ================= WEB (RENDER PING) =================
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

def run_web():
    app.run(host="0.0.0.0", port=PORT)

Thread(target=run_web, daemon=True).start()

# ================= START =================
asyncio.get_event_loop().run_until_complete(main())