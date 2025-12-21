import os
import re
import asyncio
import gc
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
from telethon.tl.functions.messages import ImportChatInviteRequest
from PIL import Image
from aiohttp import web

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

# ================= STATE =================
current_thumb = None
rename_template = None

paused = False
queue = asyncio.Queue()

# ================= CLIENT =================
client = TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH)

# ================= THUMB =================
def optimize_thumbnail(src, dst):
    img = Image.open(src).convert("RGB")
    img.thumbnail((320, 320))
    img.save(dst, "JPEG", quality=85)

# ================= HELPERS =================
def extract_episode(text, file_name):
    patterns = [
        r"[Ee][Pp][\s\-_:]*([0-9]+)",
        r"[Ee][\s\-_:]*([0-9]+)",
        r"episode[\s\-_:]*([0-9]+)",
        r"ep([0-9]+)",
        r"e([0-9]+)"
    ]
    check = (text or "") + " " + (file_name or "")
    for p in patterns:
        m = re.search(p, check)
        if m:
            return m.group(1)
    return ""

def clean_filename(name):
    return re.sub(r"[^\w\-. ]", "_", name)

async def clear_queue(q):
    dropped = 0
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
            dropped += 1
        except:
            break
    return dropped

# ================= QUEUE WORKER =================
async def worker():
    global paused

    while True:
        msg, thumb, rename = await queue.get()

        while paused:
            await asyncio.sleep(1)

        final_path = None

        try:
            await msg.reply("⬇ Downloading…")
            path = await msg.download_media(file=TMP)
            final_path = path

            if rename:
                ep = extract_episode(msg.text, msg.file.name if msg.file else "")
                name = rename.replace("{ep}", ep) + ".mp4"
                name = clean_filename(name)
                final_path = os.path.join(TMP, name)
                os.rename(path, final_path)

            await msg.reply("⬆ Uploading…")

            await client.send_file(
                TARGET_CHANNEL,
                final_path,
                caption=msg.text or "",
                thumb=thumb,
                attributes=[DocumentAttributeVideo(
                    duration=msg.video.duration if msg.video else 1,
                    w=1280,
                    h=720,
                    supports_streaming=True
                )],
                part_size_kb=256
            )

            await msg.reply("✔ Uploaded")

        except Exception as e:
            await msg.reply(f"❌ Error: {e}")

        finally:
            if final_path and os.path.exists(final_path):
                os.remove(final_path)
            gc.collect()
            queue.task_done()

# ================= EVENTS =================
@client.on(events.NewMessage)
async def handler(event):
    global current_thumb, rename_template, paused

    msg = event.message

    if not event.is_private:
        return
    if msg.peer_id.user_id != (await client.get_me()).id:
        return

    # -------- RESTART MODE --------
    if paused:
        paused = False
        dropped = await clear_queue(queue)
        await msg.reply(f"🔄 Restarted. ❌ Dropped {dropped} queued videos.")

    # -------- STOP --------
    if msg.raw_text == "/stop":
        paused = True
        await msg.reply("⏸ Paused. Queue frozen.")
        return

    # -------- RENAME --------
    if msg.raw_text.startswith("/rename"):
        parts = msg.raw_text.split(" ", 1)
        rename_template = None if len(parts) == 1 else parts[1].strip()
        await msg.reply("✏️ Rename template saved.")
        return

    # -------- THUMBNAIL --------
    if msg.photo:
        src = await msg.download_media(file=thumb_src)
        optimize_thumbnail(src, thumb_final)
        current_thumb = thumb_final
        await msg.reply("🖼 Thumbnail saved.")
        return

    # -------- VIDEO --------
    if msg.video and current_thumb:
        await queue.put((
            msg,
            current_thumb,
            rename_template
        ))
        await msg.reply("📥 Added to queue.")

# ================= AIOHTTP =================
async def health(request):
    return web.Response(text="OK")

async def web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# ================= MAIN =================
async def main():
    await client.start()
    try:
        await client(ImportChatInviteRequest(CHANNEL_INVITE))
    except:
        pass

    asyncio.create_task(worker())
    asyncio.create_task(web_server())

    await asyncio.Event().wait()

asyncio.run(main())