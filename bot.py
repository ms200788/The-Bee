import os
import re
import asyncio
import gc
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
from telethon.tl.functions.messages import ImportChatInviteRequest
from PIL import Image
import psycopg2
from flask import Flask
import threading

# ---------------- ENV ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
DATABASE_URL = os.environ["DATABASE_URL"]
TARGET_CHANNEL = int(os.environ["TARGET_CHANNEL"])
INVITE = os.environ["UPLOAD_CHANNEL_INVITE"]

BOOTSTRAP_LOGIN = os.getenv("BOOTSTRAP_LOGIN") == "1"
TG_PHONE = os.getenv("TG_PHONE")
TG_CODE = os.getenv("TG_CODE")

TMP = "/tmp/work"
os.makedirs(TMP, exist_ok=True)

MAX_SIZE = 512 * 1024 * 1024  # 512MB

# ---------------- DB -----------------
def db():
    return psycopg2.connect(DATABASE_URL)

def get_session():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS session (
            id INT PRIMARY KEY,
            data TEXT
        )
    """)
    cur.execute("SELECT data FROM session WHERE id=1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def save_session(s):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO session (id, data)
        VALUES (1, %s)
        ON CONFLICT (id)
        DO UPDATE SET data = EXCLUDED.data
    """, (s,))
    conn.commit()
    conn.close()

# ---------------- CLIENT -------------
sess = get_session()
client = TelegramClient(StringSession(sess), API_ID, API_HASH)

# ---------------- GLOBAL STATE -------
queue = asyncio.Queue()
current_thumb = None
rename_template = None

# ---------------- THUMB --------------
def optimize(src, dst):
    img = Image.open(src).convert("RGB")
    img.thumbnail((320, 320))
    img.save(dst, "JPEG", quality=80)

# ---------------- HELPERS ------------
def extract_ep(txt, name):
    m = re.search(r"[Ee][Pp]?\s?(\d+)", (txt or "") + (name or ""))
    return m.group(1) if m else ""

def clean(name):
    return re.sub(r"[^\w\-. ]", "_", name)

# ---------------- LOGIN ----------------
async def ensure_login():
    await client.connect()

    if await client.is_user_authorized():
        return

    if not BOOTSTRAP_LOGIN:
        raise RuntimeError(
            "❌ No Telegram session found. "
            "Login once locally or enable BOOTSTRAP_LOGIN."
        )

    if not TG_PHONE or not TG_CODE:
        raise RuntimeError("❌ TG_PHONE or TG_CODE missing")

    await client.send_code_request(TG_PHONE)
    await client.sign_in(phone=TG_PHONE, code=TG_CODE)

    save_session(client.session.save())
    print("✅ Telegram session saved to PostgreSQL")

# ---------------- HANDLER ------------
@client.on(events.NewMessage)
async def on_msg(e):
    global current_thumb, rename_template

    if not e.is_private:
        return

    m = e.message

    if m.text and m.text.startswith("/rename"):
        rename_template = m.text.split(" ", 1)[1] if " " in m.text else None
        await e.reply("Rename updated.")
        return

    if m.photo:
        p = await m.download_media(file=f"{TMP}/thumb.jpg")
        optimize(p, f"{TMP}/thumb_final.jpg")
        current_thumb = f"{TMP}/thumb_final.jpg"
        await e.reply("Thumbnail set.")
        return

    if not m.video and not m.document:
        return

    if m.file.size > MAX_SIZE:
        await e.reply("❌ Video >512MB rejected.")
        return

    await queue.put(m)
    await e.reply("📥 Added to queue.")

# ---------------- WORKER -------------
async def worker():
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

# ---------------- MAIN ----------------
async def main():
    await ensure_login()

    try:
        await client(ImportChatInviteRequest(INVITE))
    except:
        pass

    asyncio.create_task(worker())
    await client.run_until_disconnected()

# ---------------- WEB (UPTIME) ----------------
app = Flask(__name__)

@app.route("/")
def root():
    return "OK"

@app.route("/health")
def health():
    return {
        "status": "ok",
        "authorized": client.is_connected()
    }

def run_web():
    app.run(host="0.0.0.0", port=10000)

threading.Thread(target=run_web, daemon=True).start()

asyncio.get_event_loop().run_until_complete(main())