import os
import re
import asyncio
import gc
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
from telethon.tl.functions.messages import ImportChatInviteRequest
from PIL import Image
from flask import Flask

# ================== ENV ==================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_SESSION = os.environ.get("TG_SESSION")
TARGET_CHANNEL = int(os.environ["TARGET_CHANNEL"])
INVITE = os.environ["UPLOAD_CHANNEL_INVITE"]

if not TG_SESSION:
    raise RuntimeError("❌ TG_SESSION env missing")

# ================== PATHS =================
TMP = "/tmp/work"
os.makedirs(TMP, exist_ok=True)

MAX_SIZE = 512 * 1024 * 1024  # 512MB

# ================== CLIENT =================
client = TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH)

# ================== STATE ==================
queue = asyncio.Queue()
current_thumb = None
rename_template = None

# ================== THUMB ==================
def optimize(src, dst):
    img = Image.open(src).convert("RGB")
    img.thumbnail((320, 320))
    img.save(dst, "JPEG", quality=80)

# ================== HELPERS =================
def extract_ep(txt, name):
    m = re.search(r"[Ee][Pp]?\s?(\d+)", (txt or "") + (name or ""))
    return m.group(1) if m else ""

def clean(name):
    return re.sub(r"[^\w\-. ]", "_", name)

# ================== HANDLER =================
@client.on(events.NewMessage)
async def on_msg(e):
    global current_thumb, rename_template

    if not e.is_private:
        return

    m = e.message

    if m.text and m.text.startswith("/rename"):
        rename_template = m.text.split(" ", 1)[1] if " " in m.text else None
        await e.reply("✅ Rename template updated.")
        return

    if m.photo:
        p = await m.download_media(file=f"{TMP}/thumb.jpg")
        optimize(p, f"{TMP}/thumb_final.jpg")
        current_thumb = f"{TMP}/thumb_final.jpg"
        await e.reply("🖼 Thumbnail set.")
        return

    if not m.video and not m.document:
        return

    if m.file.size > MAX_SIZE:
        await e.reply("❌ File >512MB rejected.")
        return

    await queue.put(m)
    await e.reply("📥 Added to queue.")

# ================== WORKER =================
async def worker():
    global current_thumb

    while True:
        msg = await queue.get()
        path = None
        try:
            path = await msg.download_media(file=TMP)
            fname = msg.file.name

            if rename_template:
                ep = extract_ep(msg.text, fname)
                fname = rename_template.replace("{ep}", ep)
                if not fname.endswith(".mp4"):
                    fname += ".mp4"
                fname = clean(fname)
                new = f"{TMP}/{fname}"
                os.rename(path, new)
                path = new

            await client.send_file(
                TARGET_CHANNEL,
                path,
                caption=msg.text or "",
                thumb=current_thumb,
                attributes=[
                    DocumentAttributeVideo(
                        duration=msg.video.duration if msg.video else 0,
                        supports_streaming=True
                    )
                ],
                part_size_kb=256
            )

        finally:
            if path and os.path.exists(path):
                os.remove(path)
            gc.collect()
            queue.task_done()

# ================== MAIN =================
async def main():
    await client.start()
    try:
        await client(ImportChatInviteRequest(INVITE))
    except:
        pass

    asyncio.create_task(worker())
    await client.run_until_disconnected()

# ================== WEB ==================
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

# ================== START ==================
asyncio.get_event_loop().run_until_complete(main())