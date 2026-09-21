import os
import time
import psycopg2

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

START_BALANCE = 10.0
MINING_RATE = 0.000001


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            balance DOUBLE PRECISION DEFAULT 0,
            mining_start BIGINT DEFAULT 0,
            referrer BIGINT DEFAULT NULL
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id, username, balance, mining_start, referrer
        FROM users
        WHERE user_id = %s
        """,
        (user_id,)
    )

    user = cur.fetchone()

    cur.close()
    conn.close()

    return user


def create_user(user_id, username, referrer=None):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO users
        (user_id, username, balance, mining_start, referrer)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
        """,
        (
            user_id,
            username,
            START_BALANCE,
            0,
            referrer
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def update_mining(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance, mining_start
        FROM users
        WHERE user_id = %s
        """,
        (user_id,)
    )

    row = cur.fetchone()

    if row is None:
        cur.close()
        conn.close()
        return 0

    balance = float(row[0])
    mining_start = int(row[1])

    if mining_start > 0:

        now = int(time.time())

        minutes = (now - mining_start) // 60

        if minutes > 0:

            balance += minutes * MINING_RATE

            mining_start = now

            cur.execute(
                """
                UPDATE users
                SET balance = %s,
                    mining_start = %s
                WHERE user_id = %s
                """,
                (
                    balance,
                    mining_start,
                    user_id
                )
            )

    conn.commit()

    cur.close()
    conn.close()

    return balance


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referrer = None

    if context.args:

        try:
            referrer = int(context.args[0])

            if referrer == user.id:
                referrer = None

        except ValueError:
            referrer = None

    if get_user(user.id) is None:

        create_user(
            user.id,
            user.username or "",
            referrer
        )

    keyboard = [

        [
            InlineKeyboardButton(
                "⛏️ بدء التعدين",
                callback_data="mine"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 رصيدي",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "👥 الإحالات",
                callback_data="referrals"
            )
        ],

        [
            InlineKeyboardButton(
                "🎁 المكافأة اليومية",
                callback_data="daily"
            )
        ],

        [
            InlineKeyboardButton(
                "💸 السحب",
                callback_data="withdraw"
            )
        ]

    ]

    text = (
        "⛏️ أهلاً بك في YEM Mining\n\n"
        "💰 رصيد البداية: 10 YEM\n"
        "⚡ سرعة التعدين: 0.000001 YEM / دقيقة\n\n"
        "اختر من القائمة:"
    )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if query.data == "mine":

        user = get_user(user_id)

        if user is None:

            create_user(
                user_id,
                query.from_user.username or ""
            )

            user = get_user(user_id)

        if user[3] == 0:

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE users
                SET mining_start = %s
                WHERE user_id = %s
                """,
                (
                    int(time.time()),
                    user_id
                )
            )

            conn.commit()

            cur.close()
            conn.close()

            await query.message.reply_text(
                "⛏️ تم تشغيل التعدين!\n\n"
                "⚡ سرعتك: 0.000001 YEM / دقيقة\n"
                "يمكنك العودة لاحقًا لمتابعة رصيدك."
            )

        else:

            await query.message.reply_text(
                "⛏️ التعدين يعمل بالفعل."
            )

    elif query.data == "balance":

        balance = update_mining(user_id)

        await query.message.reply_text(
            "💰 رصيدك الحالي:\n\n"
            + str(round(balance, 6))
            + " YEM"
        )

    elif query.data == "referrals":

        bot_info = await context.bot.get_me()

        bot_username = bot_info.username

        link = (
            "https://t.me/"
            + bot_username
            + "?start="
            + str(user_id)
        )

        await query.message.reply_text(
            "👥 رابط الإحالة الخاص بك:\n\n"
            + link
            + "\n\nشارك الرابط مع أصدقائك."
        )

    elif query.data == "daily":

        await query.message.reply_text(
            "🎁 نظام المكافأة اليومية سيتم تفعيله قريبًا."
        )

    elif query.data == "withdraw":

        await query.message.reply_text(
            "💸 نظام السحب سيتم تفعيله بعد تجهيز نظام السحب."
        )


def main():

    if not TOKEN:

        print("BOT_TOKEN غير موجود")

        return

    if not DATABASE_URL:

        print("DATABASE_URL غير موجود")

        return

    init_db()

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print("YEM Mining Bot Started")

    application.run_polling()


if __name__ == "__main__":

    main()
