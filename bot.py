import os
import time
import psycopg2

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)


# =========================================================
# الإعدادات
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# رقم Telegram الخاص بالأدمن
# ضع رقم حسابك هنا
ADMIN_ID = 123456789

# رصيد البداية
START_BALANCE = 1.0

# سرعة التعدين
# 0.001 BNB كل دقيقة
MINING_RATE = 0.001

# الحد الأدنى للسحب
MIN_WITHDRAW = 0.01

# رسوم السحب
WITHDRAW_FEE = 0.0


# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():

    return psycopg2.connect(DATABASE_URL)


def init_db():

    conn = get_db()
    cur = conn.cursor()

    # جدول المستخدمين
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            balance DOUBLE PRECISION DEFAULT 0,
            mining_start BIGINT DEFAULT 0,
            referrer BIGINT DEFAULT NULL
        )
    """)

    # جدول السحوبات
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT,
            amount DOUBLE PRECISION NOT NULL,
            wallet TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            txid TEXT DEFAULT NULL,
            created_at BIGINT NOT NULL,
            processed_at BIGINT DEFAULT NULL
        )
    """)

    conn.commit()

    cur.close()
    conn.close()


# =========================================================
# المستخدم
# =========================================================

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
        (
            user_id,
            username,
            balance,
            mining_start,
            referrer
        )
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


# =========================================================
# تحديث التعدين
# =========================================================

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

        return 0.0

    balance = float(row[0])
    mining_start = int(row[1])

    if mining_start > 0:

        now = int(time.time())

        elapsed_seconds = now - mining_start

        minutes = elapsed_seconds // 60

        if minutes > 0:

            balance += minutes * MINING_RATE

            mining_start = mining_start + (minutes * 60)

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


# =========================================================
# فحص عنوان BNB
# =========================================================

def is_valid_bnb_address(wallet):

    wallet = wallet.strip()

    if not wallet.startswith("0x"):
        return False

    if len(wallet) != 42:
        return False

    for char in wallet[2:]:

        if char not in "0123456789abcdefABCDEF":
            return False

    return True


# =========================================================
# إنشاء طلب سحب
# =========================================================

def create_withdrawal(
    user_id,
    username,
    amount,
    wallet
):

    conn = get_db()
    cur = conn.cursor()

    # تحديث التعدين أولاً
    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = %s
        FOR UPDATE
        """,
        (user_id,)
    )

    row = cur.fetchone()

    if row is None:

        cur.close()
        conn.close()

        return False, "المستخدم غير موجود."

    balance = float(row[0])

    # التأكد من وجود سحب معلق
    cur.execute(
        """
        SELECT id
        FROM withdrawals
        WHERE user_id = %s
        AND status = 'pending'
        LIMIT 1
        """,
        (user_id,)
    )

    pending = cur.fetchone()

    if pending:

        cur.close()
        conn.close()

        return False, "لديك طلب سحب قيد المراجعة بالفعل."

    if amount < MIN_WITHDRAW:

        cur.close()
        conn.close()

        return False, (
            "الحد الأدنى للسحب هو "
            + str(MIN_WITHDRAW)
            + " BNB."
        )

    total_required = amount + WITHDRAW_FEE

    if balance < total_required:

        cur.close()
        conn.close()

        return False, (
            "رصيدك غير كافٍ.\n\n"
            "رصيدك: "
            + str(round(balance, 8))
            + " BNB"
        )

    # خصم المبلغ وحجزه
    new_balance = balance - total_required

    cur.execute(
        """
        UPDATE users
        SET balance = %s
        WHERE user_id = %s
        """,
        (
            new_balance,
            user_id
        )
    )

    # إنشاء الطلب
    cur.execute(
        """
        INSERT INTO withdrawals
        (
            user_id,
            username,
            amount,
            wallet,
            status,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            user_id,
            username,
            amount,
            wallet,
            "pending",
            int(time.time())
        )
    )

    withdrawal_id = cur.fetchone()[0]

    conn.commit()

    cur.close()
    conn.close()

    return True, withdrawal_id


# =========================================================
# Start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
        "⛏️ أهلاً بك في BNB Mining\n\n"
        "💰 رصيد البداية: "
        + str(START_BALANCE)
        + " BNB\n"
        "⚡ سرعة التعدين: "
        + str(MINING_RATE)
        + " BNB / دقيقة\n"
        "💸 الحد الأدنى للسحب: "
        + str(MIN_WITHDRAW)
        + " BNB\n\n"
        "اختر من القائمة:"
    )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# الأزرار
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # =====================================================
    # التعدين
    # =====================================================

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
                "🪙 العملة: BNB\n"
                "⚡ السرعة: "
                + str(MINING_RATE)
                + " BNB / دقيقة\n\n"
                "يمكنك العودة لاحقاً لمتابعة رصيدك."
            )

        else:

            await query.message.reply_text(
                "⛏️ التعدين يعمل بالفعل."
            )

    # =====================================================
    # الرصيد
    # =====================================================

    elif query.data == "balance":

        balance = update_mining(user_id)

        await query.message.reply_text(
            "💰 رصيدك الحالي:\n\n"
            + str(round(balance, 8))
            + " BNB"
        )

    # =====================================================
    # الإحالات
    # =====================================================

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
            + "\n\n"
            "شارك الرابط مع أصدقائك."
        )

    # =====================================================
    # المكافأة اليومية
    # =====================================================

    elif query.data == "daily":

        await query.message.reply_text(
            "🎁 المكافأة اليومية سيتم تفعيلها قريباً."
        )

    # =====================================================
    # السحب
    # =====================================================

    elif query.data == "withdraw":

        balance = update_mining(user_id)

        if balance < MIN_WITHDRAW:

            await query.message.reply_text(
                "💸 السحب غير متاح حالياً.\n\n"
                "💰 رصيدك: "
                + str(round(balance, 8))
                + " BNB\n"
                "📌 الحد الأدنى: "
                + str(MIN_WITHDRAW)
                + " BNB"
            )

            return

        # التحقق من وجود طلب معلق
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id
            FROM withdrawals
            WHERE user_id = %s
            AND status = 'pending'
            LIMIT 1
            """,
            (user_id,)
        )

        pending = cur.fetchone()

        cur.close()
        conn.close()

        if pending:

            await query.message.reply_text(
                "⏳ لديك طلب سحب قيد المراجعة بالفعل."
            )

            return

        context.user_data["withdraw_step"] = "amount"

        await query.message.reply_text(
            "💸 طلب سحب BNB\n\n"
            "💰 رصيدك: "
            + str(round(balance, 8))
            + " BNB\n"
            "📌 الحد الأدنى: "
            + str(MIN_WITHDRAW)
            + " BNB\n\n"
            "أرسل الآن المبلغ الذي تريد سحبه.\n\n"
            "مثال:\n"
            "0.05"
        )


# =========================================================
# استقبال بيانات السحب
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if "withdraw_step" not in context.user_data:

        return

    step = context.user_data["withdraw_step"]

    text = update.message.text.strip()

    # =====================================================
    # المبلغ
    # =====================================================

    if step == "amount":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ المبلغ غير صحيح.\n\n"
                "أرسل رقمًا مثل:\n"
                "0.05"
            )

            return

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                "❌ أقل مبلغ للسحب هو "
                + str(MIN_WITHDRAW)
                + " BNB."
            )

            return

        balance = update_mining(user_id)

        if amount + WITHDRAW_FEE > balance:

            await update.message.reply_text(
                "❌ رصيدك غير كافٍ.\n\n"
                "رصيدك الحالي: "
                + str(round(balance, 8))
                + " BNB"
            )

            return

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "wallet"

        await update.message.reply_text(
            "💳 أرسل الآن عنوان محفظة BNB.\n\n"
            "يجب أن يكون عنوان BEP-20 ويبدأ بـ 0x\n\n"
            "مثال:\n"
            "0x1234567890abcdef1234567890abcdef12345678"
        )

        return

    # =====================================================
    # عنوان المحفظة
    # =====================================================

    if step == "wallet":

        wallet = text

        if not is_valid_bnb_address(wallet):

            await update.message.reply_text(
                "❌ عنوان BNB غير صحيح.\n\n"
                "يجب أن يكون عنوان BEP-20 مكوناً من 42 حرفاً ويبدأ بـ 0x."
            )

            return

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if amount is None:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ انتهت جلسة السحب.\n"
                "اضغط السحب من القائمة وحاول مرة أخرى."
            )

            return

        success, result = create_withdrawal(
            user_id,
            update.effective_user.username or "",
            amount,
            wallet
        )

        if not success:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ لم يتم إنشاء طلب السحب.\n\n"
                + str(result)
            )

            return

        withdrawal_id = result

        context.user_data.clear()

        await update.message.reply_text(
            "✅ تم إنشاء طلب السحب بنجاح.\n\n"
            "🆔 رقم الطلب: #"
            + str(withdrawal_id)
            + "\n"
            "💰 المبلغ: "
            + str(amount)
            + " BNB\n"
            "💳 المحفظة:\n"
            + wallet
            + "\n\n"
            "⏳ حالة الطلب: قيد المراجعة\n\n"
            "سيتم مراجعة الطلب وإرسال BNB بعد الموافقة."
        )

        # إرسال الطلب للأدمن
        if ADMIN_ID != 123456789:

            try:

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "✅ موافقة",
                            callback_data="admin_approve_"
                            + str(withdrawal_id)
                        ),
                        InlineKeyboardButton(
                            "❌ رفض",
                            callback_data="admin_reject_"
                            + str(withdrawal_id)
                        )
                    ]
                ]

                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "💸 طلب سحب جديد\n\n"
                        "🆔 الطلب: #"
                        + str(withdrawal_id)
                        + "\n"
                        "👤 المستخدم: "
                        + str(user_id)
                        + "\n"
                        "👤 Username: @"
                        + (update.effective_user.username or "بدون")
                        + "\n"
                        "💰 المبلغ: "
                        + str(amount)
                        + " BNB\n"
                        "💳 المحفظة:\n"
                        + wallet
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            except Exception as e:

                print("Admin notification error:")
                print(e)


# =========================================================
# موافقة / رفض السحب
# =========================================================

async def admin_withdraw_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    admin_id = query.from_user.id

    if admin_id != ADMIN_ID:

        await query.message.reply_text(
            "❌ ليس لديك صلاحية الأدمن."
        )

        return

    data = query.data

    # =====================================================
    # موافقة
    # =====================================================

    if data.startswith("admin_approve_"):

        withdrawal_id = int(
            data.replace(
                "admin_approve_",
                ""
            )
        )

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT user_id, amount, wallet, status
            FROM withdrawals
            WHERE id = %s
            """,
            (withdrawal_id,)
        )

        row = cur.fetchone()

        if row is None:

            cur.close()
            conn.close()

            await query.message.reply_text(
                "❌ طلب السحب غير موجود."
            )

            return

        user_id = row[0]
        amount = float(row[1])
        wallet = row[2]
        status = row[3]

        if status != "pending":

            cur.close()
            conn.close()

            await query.message.reply_text(
                "⚠️ تم التعامل مع هذا الطلب مسبقاً."
            )

            return

        cur.execute(
            """
            UPDATE withdrawals
            SET status = 'approved',
                processed_at = %s
            WHERE id = %s
            """,
            (
                int(time.time()),
                withdrawal_id
            )
        )

        conn.commit()

        cur.close()
        conn.close()

        await query.message.reply_text(
            "✅ تمت الموافقة على طلب السحب.\n\n"
            "🆔 الطلب: #"
            + str(withdrawal_id)
            + "\n"
            "💰 المبلغ: "
            + str(amount)
            + " BNB\n"
            "💳 المحفظة:\n"
            + wallet
            + "\n\n"
            "⚠️ أرسل BNB يدوياً إلى العنوان، "
            "ثم سجّل TXID."
        )

        try:

            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ تمت الموافقة على طلب السحب.\n\n"
                    "🆔 الطلب: #"
                    + str(withdrawal_id)
                    + "\n"
                    "💰 المبلغ: "
                    + str(amount)
                    + " BNB\n\n"
                    "سيتم إرسال المبلغ إلى محفظتك."
                )
            )

        except Exception as e:

            print(e)

    # =====================================================
    # رفض
    # =====================================================

    elif data.startswith("admin_reject_"):

        withdrawal_id = int(
            data.replace(
                "admin_reject_",
                ""
            )
        )

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT user_id, amount, status
            FROM withdrawals
            WHERE id = %s
            """,
            (withdrawal_id,)
        )

        row = cur.fetchone()

        if row is None:

            cur.close()
            conn.close()

            await query.message.reply_text(
                "❌ طلب السحب غير موجود."
            )

            return

        user_id = row[0]
        amount = float(row[1])
        status = row[2]

        if status != "pending":

            cur.close()
            conn.close()

            await query.message.reply_text(
                "⚠️ تم التعامل مع هذا الطلب مسبقاً."
            )

            return

        # إعادة المبلغ للمستخدم
        cur.execute(
            """
            UPDATE users
            SET balance = balance + %s
            WHERE user_id = %s
            """,
            (
                amount + WITHDRAW_FEE,
                user_id
            )
        )

        cur.execute(
            """
            UPDATE withdrawals
            SET status = 'rejected',
                processed_at = %s
            WHERE id = %s
            """,
            (
                int(time.time()),
                withdrawal_id
            )
        )

        conn.commit()

        cur.close()
        conn.close()

        await query.message.reply_text(
            "❌ تم رفض طلب السحب.\n\n"
            "🆔 الطلب: #"
            + str(withdrawal_id)
            + "\n"
            "💰 تم إعادة المبلغ إلى حساب المستخدم."
        )

        try:

            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ تم رفض طلب السحب.\n\n"
                    "🆔 الطلب: #"
                    + str(withdrawal_id)
                    + "\n\n"
                    "💰 تمت إعادة المبلغ إلى رصيدك."
                )
            )

        except Exception as e:

            print(e)


# =========================================================
# أوامر الأدمن
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ ليس لديك صلاحية."
        )

        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            user_id,
            amount,
            wallet,
            status,
            created_at
        FROM withdrawals
        ORDER BY id DESC
        LIMIT 20
        """
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:

        await update.message.reply_text(
            "📭 لا توجد طلبات سحب."
        )

        return

    text = "📋 آخر طلبات السحب:\n\n"

    for row in rows:

        withdrawal_id = row[0]
        user_id = row[1]
        amount = row[2]
        wallet = row[3]
        status = row[4]

        text += (
            "🆔 #"
            + str(withdrawal_id)
            + "\n"
            "👤 "
            + str(user_id)
            + "\n"
            "💰 "
            + str(amount)
            + " BNB\n"
            "💳 "
            + wallet
            + "\n"
            "📌 "
            + status
            + "\n\n"
        )

    await update.message.reply_text(text)


# =========================================================
# تشغيل البوت
# =========================================================

def main():

    if not TOKEN:

        print("BOT_TOKEN غير موجود")

        return

    if not DATABASE_URL:

        print("DATABASE_URL غير موجود")

        return

    try:

        init_db()

        print("PostgreSQL connected")

    except Exception as e:

        print("Database Error:")
        print(e)

        return

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # Start
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # قائمة الأدمن
    application.add_handler(
        CommandHandler(
            "withdrawals",
            admin_command
        )
    )

    # أزرار السحب للأدمن
    application.add_handler(
        CallbackQueryHandler(
            admin_withdraw_handler,
            pattern=r"^admin_(approve|reject)_"
        )
    )

    # أزرار المستخدم
    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # استقبال مبلغ وعنوان السحب
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    print("BNB Mining Bot Started")

    application.run_polling()


if __name__ == "__main__":

    main()
