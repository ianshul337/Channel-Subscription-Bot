import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread
from urllib.parse import quote


# =========================================================
# RENDER KEEP-ALIVE WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running and healthy!"


def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_web, daemon=True).start()


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
UPI_ID = os.getenv("UPI_ID")
CONTACT_USERNAME = os.getenv("CONTACT_USERNAME", "").replace("@", "")


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing")

if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable is missing")

if not UPI_ID:
    raise ValueError("UPI_ID environment variable is missing")


# =========================================================
# BOT + DATABASE
# =========================================================

bot = telebot.TeleBot(BOT_TOKEN)

client = MongoClient(MONGO_URI)
db = client["sub_management"]

channels_col = db["channels"]
users_col = db["users"]


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def format_duration(minutes):
    """
    Convert stored minutes into a readable duration.
    """

    minutes = int(minutes)

    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} Day" if days == 1 else f"{days} Days"

    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} Hour" if hours == 1 else f"{hours} Hours"

    return f"{minutes} Minutes"


def parse_duration(duration):
    """
    Convert:
    60m -> 60 minutes
    12h -> 720 minutes
    1d  -> 1440 minutes
    """

    duration = duration.strip().lower()

    if len(duration) < 2:
        raise ValueError("Invalid duration")

    number = duration[:-1]
    unit = duration[-1]

    if not number.isdigit():
        raise ValueError("Invalid duration number")

    number = int(number)

    if number <= 0:
        raise ValueError("Duration must be greater than zero")

    if unit == "m":
        minutes = number

    elif unit == "h":
        minutes = number * 60

    elif unit == "d":
        minutes = number * 1440

    else:
        raise ValueError("Use m, h or d")

    return minutes


def make_contact_button():
    markup = InlineKeyboardMarkup()

    if CONTACT_USERNAME:
        markup.add(
            InlineKeyboardButton(
                "📞 Contact Admin",
                url=f"https://t.me/{CONTACT_USERNAME}"
            )
        )

    return markup


# =========================================================
# START COMMAND
# =========================================================

@bot.message_handler(commands=["start"])
def start_handler(message):

    user_id = message.from_user.id
    parts = message.text.split()

    # -----------------------------------------------------
    # USER DEEP LINK
    # -----------------------------------------------------

    if len(parts) > 1:

        try:
            ch_id = int(parts[1])

            ch_data = channels_col.find_one(
                {"channel_id": ch_id}
            )

            if ch_data:

                markup = InlineKeyboardMarkup()

                plans = ch_data.get("plans", {})

                for minutes, price in plans.items():

                    duration_text = format_duration(minutes)

                    markup.add(
                        InlineKeyboardButton(
                            f"💳 {duration_text} - ₹{price}",
                            callback_data=f"select_{ch_id}_{minutes}"
                        )
                    )

                if CONTACT_USERNAME:
                    markup.add(
                        InlineKeyboardButton(
                            "📞 Contact Admin",
                            url=f"https://t.me/{CONTACT_USERNAME}"
                        )
                    )

                bot.send_message(
                    message.chat.id,

                    f"👋 Welcome!\n\n"
                    f"📢 Channel: *{ch_data['name']}*\n\n"
                    f"💳 Select your subscription plan below:",

                    reply_markup=markup,
                    parse_mode="Markdown"
                )

                return

        except Exception as e:
            print("Start deep-link error:", e)

    # -----------------------------------------------------
    # ADMIN PANEL
    # -----------------------------------------------------

    if user_id == ADMIN_ID:

        bot.send_message(
            message.chat.id,

            "✅ *Admin Panel Active!*\n\n"

            "/add - Add/Edit Channel & Plans\n"
            "/channels - Manage Existing Channels",

            parse_mode="Markdown"
        )

    else:

        bot.send_message(
            message.chat.id,
            "👋 Welcome!\n\n"
            "To join a channel, please use the subscription link provided by Admin."
        )


# =========================================================
# CHANNEL LIST
# =========================================================

@bot.message_handler(
    commands=["channels"],
    func=lambda m: m.from_user.id == ADMIN_ID
)
def list_channels(message):

    markup = InlineKeyboardMarkup()

    cursor = channels_col.find(
        {"admin_id": ADMIN_ID}
    )

    count = 0

    for ch in cursor:

        markup.add(
            InlineKeyboardButton(
                f"📢 {ch['name']}",
                callback_data=f"manage_{ch['channel_id']}"
            )
        )

        count += 1

    markup.add(
        InlineKeyboardButton(
            "➕ Add New Channel",
            callback_data="add_new"
        )
    )

    if count == 0:

        bot.send_message(
            ADMIN_ID,
            "No channels found.\n\nClick below to add one.",
            reply_markup=markup
        )

    else:

        bot.send_message(
            ADMIN_ID,
            "📋 *Your Managed Channels:*",
            reply_markup=markup,
            parse_mode="Markdown"
        )


# =========================================================
# ADD CHANNEL
# =========================================================

@bot.message_handler(
    commands=["add"],
    func=lambda m: m.from_user.id == ADMIN_ID
)
def add_channel_start(message):

    msg = bot.send_message(
        ADMIN_ID,

        "📢 *Add / Edit Channel*\n\n"

        "First make sure the bot is Admin in your channel.\n\n"

        "Then FORWARD any message from that channel here.",

        parse_mode="Markdown"
    )

    bot.register_next_step_handler(
        msg,
        get_plans
    )


# =========================================================
# ADD NEW CHANNEL CALLBACK
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "add_new"
)
def cb_add_new(call):

    bot.answer_callback_query(call.id)

    msg = bot.send_message(
        ADMIN_ID,

        "📢 Forward any message from your channel here."
    )

    bot.register_next_step_handler(
        msg,
        get_plans
    )


# =========================================================
# GET CHANNEL
# =========================================================

def get_plans(message):

    if message.forward_from_chat:

        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title

        msg = bot.send_message(
            ADMIN_ID,

            f"📢 *Channel Detected:*\n"
            f"{ch_name}\n\n"

            "💳 *Enter subscription plans:*\n\n"

            "Examples:\n"
            "`1d:99` → 1 Day ₹99\n"
            "`7d:149` → 7 Days ₹149\n"
            "`30d:199` → 30 Days ₹199\n"
            "`12h:49` → 12 Hours ₹49\n"
            "`60m:20` → 60 Minutes ₹20\n\n"

            "Multiple plans:\n"
            "`1d:99, 7d:149, 30d:199`\n\n"

            "Units:\n"
            "m = Minutes\n"
            "h = Hours\n"
            "d = Days",

            parse_mode="Markdown"
        )

        bot.register_next_step_handler(
            msg,
            finalize_channel,
            ch_id,
            ch_name
        )

    else:

        bot.send_message(
            ADMIN_ID,

            "❌ Message was not forwarded.\n\n"
            "Use /add and forward a message from your channel."
        )


# =========================================================
# SAVE CHANNEL + PLANS
# =========================================================

def finalize_channel(message, ch_id, ch_name):

    try:

        text = (message.text or "").strip()

        if not text:
            raise ValueError("Empty plan")

        raw_plans = text.split(",")

        plans_dict = {}

        for plan in raw_plans:

            plan = plan.strip()

            if not plan:
                continue

            if ":" not in plan:
                raise ValueError("Missing colon")

            duration, price = plan.split(":", 1)

            duration = duration.strip().lower()
            price = price.strip()

            # Validate price
            if not price.isdigit():
                raise ValueError("Invalid price")

            price_value = int(price)

            if price_value <= 0:
                raise ValueError("Price must be greater than zero")

            # Convert duration to minutes
            minutes = parse_duration(duration)

            # Save internally as minutes
            plans_dict[str(minutes)] = str(price_value)

        if not plans_dict:
            raise ValueError("No valid plans")

        # Save to MongoDB
        channels_col.update_one(

            {"channel_id": ch_id},

            {
                "$set": {
                    "name": ch_name,
                    "plans": plans_dict,
                    "admin_id": ADMIN_ID
                }
            },

            upsert=True
        )

        bot_username = bot.get_me().username

        invite_link = (
            f"https://t.me/{bot_username}?start={ch_id}"
        )

        bot.send_message(

            ADMIN_ID,

            "✅ *Setup Successful!*\n\n"

            f"📢 Channel: {ch_name}\n"
            f"💳 Plans Added: {len(plans_dict)}\n\n"

            "🔗 *User Subscription Link:*\n"
            f"`{invite_link}`",

            parse_mode="Markdown"
        )

    except Exception as e:
    print(f"PLAN ERROR: {type(e).__name__}: {e}")

    bot.send_message(
        ADMIN_ID,
        f"❌ ERROR:\n\n"
        f"Type: `{type(e).__name__}`\n"
        f"Details: `{e}`",
        parse_mode="Markdown"
    )


# =========================================================
# USER SELECTS PLAN
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("select_")
)
def user_pays(call):

    try:

        bot.answer_callback_query(call.id)

        _, ch_id, minutes = call.data.split("_")

        ch_id = int(ch_id)

        ch_data = channels_col.find_one(
            {"channel_id": ch_id}
        )

        if not ch_data:
            bot.send_message(
                call.message.chat.id,
                "❌ Channel not found."
            )
            return

        price = ch_data["plans"][minutes]

        duration_text = format_duration(minutes)

        # UPI payment URI
        upi_data = (
            f"upi://pay?"
            f"pa={UPI_ID}&"
            f"am={price}&"
            f"cu=INR"
        )

        qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=300x300&data={quote(upi_data)}"
        )

        markup = InlineKeyboardMarkup()

        markup.add(
            InlineKeyboardButton(
                "✅ I Have Paid",
                callback_data=f"paid_{ch_id}_{minutes}"
            )
        )

        if CONTACT_USERNAME:

            markup.add(
                InlineKeyboardButton(
                    "📞 Contact Admin",
                    url=f"https://t.me/{CONTACT_USERNAME}"
                )
            )

        bot.send_photo(

            call.message.chat.id,

            qr_url,

            caption=(
                f"💳 *Subscription Payment*\n\n"

                f"📦 Plan: *{duration_text}*\n"
                f"💰 Price: *₹{price}*\n\n"

                f"📱 UPI ID:\n`{UPI_ID}`\n\n"

                "Scan the QR and complete the payment.\n\n"
                "After payment, click *I Have Paid*."
            ),

            reply_markup=markup,
            parse_mode="Markdown"
        )

    except Exception as e:

        print("Payment screen error:", e)

        bot.send_message(
            call.message.chat.id,
            "❌ Something went wrong. Please try again."
        )


# =========================================================
# USER SAYS PAID
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("paid_")
)
def admin_notify(call):

    try:

        bot.answer_callback_query(call.id)

        _, ch_id, minutes = call.data.split("_")

        ch_id = int(ch_id)

        user = call.from_user

        ch_data = channels_col.find_one(
            {"channel_id": ch_id}
        )

        if not ch_data:
            return

        price = ch_data["plans"][minutes]

        duration_text = format_duration(minutes)

        markup = InlineKeyboardMarkup()

        markup.add(
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"app_{user.id}_{ch_id}_{minutes}"
            )
        )

        markup.add(
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"rej_{user.id}"
            )
        )

        bot.send_message(

            ADMIN_ID,

            "🔔 *Payment Verification Required!*\n\n"

            f"👤 User: {user.first_name}\n"
            f"🆔 User ID: `{user.id}`\n"
            f"📢 Channel: {ch_data['name']}\n"
            f"📦 Plan: {duration_text}\n"
            f"💰 Price: ₹{price}",

            reply_markup=markup,
            parse_mode="Markdown"
        )

        contact_markup = InlineKeyboardMarkup()

        if CONTACT_USERNAME:

            contact_markup.add(
                InlineKeyboardButton(
                    "📞 Contact Admin",
                    url=f"https://t.me/{CONTACT_USERNAME}"
                )
            )

        bot.send_message(

            call.message.chat.id,

            "✅ *Payment request sent!*\n\n"
            "Please wait for Admin approval.",

            reply_markup=contact_markup,
            parse_mode="Markdown"
        )

    except Exception as e:

        print("Admin notification error:", e)


# =========================================================
# APPROVE PAYMENT
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("app_")
)
def approve_now(call):

    try:

        bot.answer_callback_query(call.id)

        _, u_id, ch_id, minutes = call.data.split("_")

        u_id = int(u_id)
        ch_id = int(ch_id)
        minutes = int(minutes)

        ch_data = channels_col.find_one(
            {"channel_id": ch_id}
        )

        if not ch_data:
            raise ValueError("Channel not found")

        duration_text = format_duration(minutes)

        # Calculate expiry
        expiry_datetime = (
            datetime.now() +
            timedelta(minutes=minutes)
        )

        expiry_ts = int(
            expiry_datetime.timestamp()
        )

        # Create single-use invite link
        link = bot.create_chat_invite_link(

            ch_id,

            member_limit=1,

            expire_date=expiry_ts
        )

        # Save subscription
        users_col.update_one(

            {
                "user_id": u_id,
                "channel_id": ch_id
            },

            {
                "$set": {
                    "expiry": expiry_datetime.timestamp(),
                    "minutes": minutes,
                    "approved_at": datetime.now()
                }
            },

            upsert=True
        )

        # Send user link
        bot.send_message(

            u_id,

            "🥳 *Payment Approved!*\n\n"

            f"📢 Channel: {ch_data['name']}\n"
            f"📦 Subscription: {duration_text}\n\n"

            f"🔗 *Join Link:*\n"
            f"{link.invite_link}\n\n"

            f"⚠️ This subscription expires after {duration_text}.",

            parse_mode="Markdown"
        )

        # Update admin message
        bot.edit_message_text(

            f"✅ *Payment Approved!*\n\n"
            f"User ID: `{u_id}`\n"
            f"Plan: {duration_text}",

            call.message.chat.id,
            call.message.message_id,

            parse_mode="Markdown"
        )

    except Exception as e:

        print("Approval error:", e)

        bot.send_message(
            ADMIN_ID,
            f"❌ Approval Error:\n`{e}`",
            parse_mode="Markdown"
        )


# =========================================================
# REJECT PAYMENT
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("rej_")
)
def reject_payment(call):

    try:

        bot.answer_callback_query(call.id)

        _, user_id = call.data.split("_")

        user_id = int(user_id)

        bot.send_message(

            user_id,

            "❌ *Payment Request Rejected.*\n\n"
            "If you believe this is a mistake, please contact Admin.",

            parse_mode="Markdown"
        )

        bot.edit_message_text(

            "❌ *Payment Rejected.*",

            call.message.chat.id,
            call.message.message_id,

            parse_mode="Markdown"
        )

    except Exception as e:

        print("Reject error:", e)


# =========================================================
# MANAGE CHANNEL
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("manage_")
)
def manage_ch(call):

    try:

        bot.answer_callback_query(call.id)

        ch_id = int(
            call.data.split("_")[1]
        )

        ch_data = channels_col.find_one(
            {"channel_id": ch_id}
        )

        if not ch_data:
            return

        bot_username = bot.get_me().username

        link = (
            f"https://t.me/{bot_username}?start={ch_id}"
        )

        plans_text = []

        for minutes, price in ch_data.get("plans", {}).items():

            plans_text.append(
                f"• {format_duration(minutes)} — ₹{price}"
            )

        plans_display = "\n".join(plans_text)

        bot.edit_message_text(

            f"⚙️ *Channel Settings*\n\n"

            f"📢 Channel: {ch_data['name']}\n\n"

            f"💳 *Plans:*\n"
            f"{plans_display}\n\n"

            f"🔗 *User Link:*\n"
            f"`{link}`\n\n"

            "To edit plans, use /add again and "
            "forward a message from this channel.",

            call.message.chat.id,
            call.message.message_id,

            parse_mode="Markdown"
        )

    except Exception as e:

        print("Manage channel error:", e)


# =========================================================
# AUTOMATICALLY REMOVE EXPIRED USERS
# =========================================================

def kick_expired_users():

    try:

        now = datetime.now().timestamp()

        expired_users = users_col.find(
            {
                "expiry": {
                    "$lte": now
                }
            }
        )

        bot_username = bot.get_me().username

        for user in expired_users:

            try:

                channel_id = user["channel_id"]
                user_id = user["user_id"]

                # Ban user
                bot.ban_chat_member(
                    channel_id,
                    user_id
                )

                # Immediately unban so they can rejoin later
                bot.unban_chat_member(
                    channel_id,
                    user_id
                )

                rejoin_url = (
                    f"https://t.me/{bot_username}"
                    f"?start={channel_id}"
                )

                markup = InlineKeyboardMarkup()

                markup.add(
                    InlineKeyboardButton(
                        "🔄 Re-join / Renew",
                        url=rejoin_url
                    )
                )

                bot.send_message(

                    user_id,

                    "⚠️ *Subscription Expired!*\n\n"

                    "Your subscription has expired.\n\n"
                    "Click below to renew your subscription.",

                    reply_markup=markup,
                    parse_mode="Markdown"
                )

                users_col.delete_one(
                    {"_id": user["_id"]}
                )

            except Exception as e:

                print(
                    f"Expired user error: {e}"
                )

    except Exception as e:

        print(
            f"Expiry scheduler error: {e}"
        )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":

    print("Starting bot...")

    # Start Render web server
    keep_alive()

    # Start expiry scheduler
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        kick_expired_users,
        "interval",
        minutes=1
    )

    scheduler.start()

    # Start Telegram bot
    bot.remove_webhook()

    print("Bot is running...")

    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=10
    )
