import os
import re
import threading
from flask import Flask, jsonify

from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext
)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# ================= FLASK APP =================
app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


# ================= MEMORY =================
caption_template = {}   # uid -> template
media_queue = {}        # uid -> list of dicts
# ==========================================


# -------- STRICT EP EXTRACTOR --------
def extract_episode(text):
    if not text:
        return None

    pattern = re.compile(
        r'(?:episode|ep|e)\s*[-:#]?\s*(\d{1,4})',
        re.IGNORECASE
    )
    m = pattern.search(text)
    return int(m.group(1)) if m else None


# -------- /caption --------
def caption_cmd(update: Update, context: CallbackContext):
    uid = update.effective_user.id

    if not context.args:
        update.message.reply_text(
            "❌ Usage:\n/caption <template with {ep}>"
        )
        return

    caption_template[uid] = update.message.text.split(" ", 1)[1]
    media_queue[uid] = []

    update.message.reply_text(
        "✅ Caption saved.\nSend up to 99 files."
    )


# -------- MEDIA COLLECTOR --------
def collect_media(update: Update, context: CallbackContext):
    uid = update.effective_user.id

    if uid not in caption_template:
        return

    if len(media_queue[uid]) >= 99:
        update.message.reply_text("⚠️ Limit reached (99 files).")
        return

    msg = update.message
    original_caption = msg.caption or ""
    ep = extract_episode(original_caption)

    if ep is None:
        update.message.reply_text("❌ Episode not found in caption.")
        return

    media_queue[uid].append({
        "ep": ep,
        "message": msg
    })


# -------- PROCESS QUEUE (SORTED) --------
def process_queue(update: Update, context: CallbackContext, target):
    uid = update.effective_user.id

    if uid not in media_queue or not media_queue[uid]:
        update.message.reply_text("❌ No files queued.")
        return

    template = caption_template[uid]

    # SORT BY EPISODE ASC
    items = sorted(media_queue[uid], key=lambda x: x["ep"])

    for item in items:
        msg = item["message"]
        ep = item["ep"]

        final_caption = (
            template.replace("{ep}", str(ep))
            if "{ep}" in template
            else template
        )

        if msg.photo:
            context.bot.send_photo(
                chat_id=target,
                photo=msg.photo[-1].file_id,
                caption=final_caption
            )

        elif msg.video:
            context.bot.send_video(
                chat_id=target,
                video=msg.video.file_id,
                caption=final_caption
            )

        elif msg.document:
            context.bot.send_document(
                chat_id=target,
                document=msg.document.file_id,
                caption=final_caption
            )

    # CLEAR MEMORY
    caption_template.pop(uid, None)
    media_queue.pop(uid, None)

    update.message.reply_text("✅ Done")


# -------- /give --------
def give_cmd(update: Update, context: CallbackContext):
    process_queue(update, context, update.effective_chat.id)


# -------- /forward --------
def forward_cmd(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text(
            "❌ Usage:\n/forward <channel_id>"
        )
        return

    target = context.args[0]
    process_queue(update, context, target)


# -------- BOT RUNNER --------
def run_bot():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("caption", caption_cmd))
    dp.add_handler(CommandHandler("give", give_cmd))
    dp.add_handler(CommandHandler("forward", forward_cmd))

    dp.add_handler(
        MessageHandler(
            Filters.photo | Filters.video | Filters.document,
            collect_media
        )
    )

    updater.start_polling()
    updater.idle()


# -------- MAIN --------
if __name__ == "__main__":
    # Flask thread (for /health)
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram bot polling
    run_bot()