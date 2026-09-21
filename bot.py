import os
import time
import threading
import psycopg2

from web3 import Web3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

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

# ضع Telegram ID الخاص بك هنا
ADMIN_ID = 6494612745

# =========================================================
# BNB / BSC
# =========================================================

BSC_RPC_URL = os.environ.get(
    "BSC_RPC_URL",
    "https://bsc-dataseed.bnbchain.org"
)

BSC_CHAIN_ID = 56

PRIVATE_KEY = os.environ.get(
    "HOT_WALLET_PRIVATE_KEY"
)

# =========================================================
# إعدادات التعدين
# =========================================================

START_BALANCE = 1.0

# 0.001 BNB لكل دقيقة
MINING_RATE = 0.001

# =========================================================
# إعدادات السحب
# =========================================================

MIN_WITHDRAW = 0.01

# رسوم إضافية اختيارية
WITHDRAW_FEE = 0.0


# =========================================================
# Web3
# =========================================================

w3 = None
hot_account = None

withdraw_lock = threading.Lock()


def init_blockchain():

    global w3
    global hot_account

    if not PRIVATE_KEY:

        print("HOT_WALLET_PRIVATE_KEY غير موجود")

        return False

    try:

        w3 = Web3(
            Web3.HTTPProvider(
                BSC_RPC_URL,
                request_kwargs={
                    "timeout": 30
                }
            )
        )

        if not w3.is_connected():

            print("فشل الاتصال بشبكة BSC")

            return False

        hot_account = w3.eth.account.from_key(
            PRIVATE_KEY
        )

        network_id = w3.eth.chain_id

        print(
            "BSC connected. Chain ID:",
            network_id
        )

        print(
            "Hot wallet:",
            hot_account.address
        )

        if network_id != BSC_CHAIN_ID:

            print(
                "خطأ: RPC ليس BSC Mainnet."
            )

            return False

        return True

    except Exception as e:

        print("Blockchain Error:")
        print(e)

        return False


# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():

    return psycopg2.connect(
        DATABASE_URL
    )


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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT,
            amount DOUBLE PRECISION NOT NULL,
            wallet TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            txid TEXT DEFAULT NULL,
            error_message TEXT DEFAULT NULL,
            created_at BIGINT NOT NULL,
            processed_at BIGINT DEFAULT NULL
        )
    """)

    # إضافة الأعمدة إذا كانت قاعدة البيانات قديمة
    cur.execute("""
        ALTER TABLE withdrawals
        ADD COLUMN IF NOT EXISTS error_message TEXT DEFAULT NULL
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
        SELECT
            user_id,
            username,
            balance,
            mining_start,
            referrer
        FROM users
        WHERE user_id = %s
        """,
        (user_id,)
    )

    user = cur.fetchone()

    cur.close()
    conn.close()

    return user


def create_user(
    user_id,
    username,
    referrer=None
):

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

        ON CONFLICT (user_id)
        DO NOTHING
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

        elapsed = now - mining_start

        minutes = elapsed // 60

        if minutes > 0:

            balance += (
                minutes * MINING_RATE
            )

            mining_start += (
                minutes * 60
            )

            cur.execute(
                """
                UPDATE users
                SET
                    balance = %s,
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

    try:

        int(wallet[2:], 16)

        return True

    except Exception:

        return False


# =========================================================
# رصيد محفظة البوت
# =========================================================

def get_hot_wallet_balance():

    if w3 is None:
        return 0.0

    if hot_account is None:
        return 0.0

    try:

        balance_wei = w3.eth.get_balance(
            hot_account.address
        )

        return float(
            w3.from_wei(
                balance_wei,
                "ether"
            )
        )

    except Exception as e:

        print(
            "Wallet balance error:",
            e
        )

        return 0.0


# =========================================================
# إرسال BNB حقيقي
# =========================================================

def send_bnb(
    destination,
    amount
):

    if w3 is None:

        return False, None, (
            "Blockchain غير متصل."
        )

    if hot_account is None:

        return False, None, (
            "محفظة الإرسال غير جاهزة."
        )

    try:

        destination = w3.to_checksum_address(
            destination
        )

        amount_wei = w3.to_wei(
            amount,
            "ether"
        )

        wallet_balance = w3.eth.get_balance(
            hot_account.address
        )

        gas_price = w3.eth.gas_price

        gas_limit = 21000

        gas_cost = (
            gas_price * gas_limit
        )

        required = (
            amount_wei + gas_cost
        )

        if wallet_balance < required:

            return False, None, (
                "رصيد محفظة البوت غير كافٍ "
                "لتغطية المبلغ ورسوم الشبكة."
            )

        nonce = w3.eth.get_transaction_count(
            hot_account.address,
            "pending"
        )

        transaction = {

            "nonce": nonce,

            "to": destination,

            "value": amount_wei,

            "gas": gas_limit,

            "gasPrice": gas_price,

            "chainId": BSC_CHAIN_ID
        }

        signed = w3.eth.account.sign_transaction(
            transaction,
            PRIVATE_KEY
        )

        tx_hash = w3.eth.send_raw_transaction(
            signed.raw_transaction
        )

        txid = tx_hash.hex()

        print(
            "BNB transaction sent:",
            txid
        )

        return True, txid, None

    except Exception as e:

        print(
            "Send BNB Error:",
            e
        )

        return False, None, str(e)


# =========================================================
# إنشاء طلب السحب
# =========================================================

def create_withdrawal(
    user_id,
    username,
    amount,
    wallet
):

    conn = get_db()
    cur = conn.cursor()

    try:

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

            conn.rollback()

            return False, (
                "المستخدم غير موجود."
            )

        balance = float(row[0])

        # منع أكثر من طلب معلق
        cur.execute(
            """
            SELECT id
            FROM withdrawals
            WHERE user_id = %s
            AND status IN (
                'pending',
                'processing'
            )
            LIMIT 1
            """,
            (user_id,)
        )

        if cur.fetchone():

            conn.rollback()

            return False, (
                "لديك طلب سحب قيد المعالجة بالفعل."
            )

        if amount < MIN_WITHDRAW:

            conn.rollback()

            return False, (
                "الحد الأدنى للسحب هو "
                + str(MIN_WITHDRAW)
                + " BNB."
            )

        total = (
            amount + WITHDRAW_FEE
        )

        if balance < total:

            conn.rollback()

            return False, (
                "رصيدك غير كافٍ."
            )

        # حجز الرصيد
        new_balance = (
            balance - total
        )

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
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                'pending',
                %s
            )
            RETURNING id
            """,
            (
                user_id,
                username,
                amount,
                wallet,
                int(time.time())
            )
        )

        withdrawal_id = cur.fetchone()[0]

        conn.commit()

        return True, withdrawal_id

    except Exception as e:

        conn.rollback()

        print(
            "Create withdrawal error:",
            e
        )

        return False, str(e)

    finally:

        cur.close()
        conn.close()


# =========================================================
# معالجة السحب تلقائياً
# =========================================================

def process_withdrawal(
    withdrawal_id
):

    with withdraw_lock:

        conn = get_db()
        cur = conn.cursor()

        try:

            # قفل الطلب
            cur.execute(
                """
                SELECT
                    user_id,
                    amount,
                    wallet,
                    status
                FROM withdrawals
                WHERE id = %s
                FOR UPDATE
                """,
                (withdrawal_id,)
            )

            row = cur.fetchone()

            if row is None:

                conn.rollback()

                return False, (
                    "طلب السحب غير موجود."
                )

            user_id = row[0]
            amount = float(row[1])
            wallet = row[2]
            status = row[3]

            if status != "pending":

                conn.rollback()

                return False, (
                    "تمت معالجة الطلب مسبقاً."
                )

            # تحويل الحالة إلى processing
            cur.execute(
                """
                UPDATE withdrawals
                SET status = 'processing'
                WHERE id = %s
                """,
                (withdrawal_id,)
            )

            conn.commit()

        except Exception as e:

            conn.rollback()

            cur.close()
            conn.close()

            return False, str(e)

        cur.close()
        conn.close()

        # إرسال BNB خارج قاعدة البيانات
        success, txid, error = send_bnb(
            wallet,
            amount
        )

        conn = get_db()
        cur = conn.cursor()

        try:

            if success:

                cur.execute(
                    """
                    UPDATE withdrawals
                    SET
                        status = 'completed',
                        txid = %s,
                        processed_at = %s
                    WHERE id = %s
                    """,
                    (
                        txid,
                        int(time.time()),
                        withdrawal_id
                    )
                )

                conn.commit()

                return True, txid

            else:

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
                    SET
                        status = 'failed',
                        error_message = %s,
                        processed_at = %s
                    WHERE id = %s
                    """,
                    (
                        error,
                        int(time.time()),
                        withdrawal_id
                    )
                )

                conn.commit()

                return False, error

        except Exception as e:

            conn.rollback()

            print(
                "Process withdrawal DB error:",
                e
            )

            return False, str(e)

        finally:

            cur.close()
            conn.close()


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referrer = None

    if context.args:

        try:

            referrer = int(
                context.args[0]
            )

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
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# أزرار المستخدم
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # -----------------------------------------------------
    # التعدين
    # -----------------------------------------------------

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
                + " BNB / دقيقة"
            )

        else:

            await query.message.reply_text(
                "⛏️ التعدين يعمل بالفعل."
            )

    # -----------------------------------------------------
    # الرصيد
    # -----------------------------------------------------

    elif query.data == "balance":

        balance = update_mining(
            user_id
        )

        await query.message.reply_text(
            "💰 رصيدك الحالي:\n\n"
            + str(round(balance, 8))
            + " BNB"
        )

    # -----------------------------------------------------
    # الإحالات
    # -----------------------------------------------------

    elif query.data == "referrals":

        bot_info = await context.bot.get_me()

        link = (
            "https://t.me/"
            + bot_info.username
            + "?start="
            + str(user_id)
        )

        await query.message.reply_text(
            "👥 رابط الإحالة الخاص بك:\n\n"
            + link
        )

    # -----------------------------------------------------
    # اليومية
    # -----------------------------------------------------

    elif query.data == "daily":

        await query.message.reply_text(
            "🎁 المكافأة اليومية سيتم تفعيلها لاحقاً."
        )

    # -----------------------------------------------------
    # السحب
    # -----------------------------------------------------

    elif query.data == "withdraw":

        balance = update_mining(
            user_id
        )

        if balance < MIN_WITHDRAW:

            await query.message.reply_text(
                "💸 لا يمكنك السحب حالياً.\n\n"
                "💰 رصيدك: "
                + str(round(balance, 8))
                + " BNB\n"
                "📌 الحد الأدنى: "
                + str(MIN_WITHDRAW)
                + " BNB"
            )

            return

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id
            FROM withdrawals
            WHERE user_id = %s
            AND status IN (
                'pending',
                'processing'
            )
            LIMIT 1
            """,
            (user_id,)
        )

        pending = cur.fetchone()

        cur.close()
        conn.close()

        if pending:

            await query.message.reply_text(
                "⏳ لديك سحب قيد المعالجة بالفعل."
            )

            return

        context.user_data[
            "withdraw_step"
        ] = "amount"

        await query.message.reply_text(
            "💸 سحب BNB إلى Binance\n\n"
            "💰 رصيدك: "
            + str(round(balance, 8))
            + " BNB\n"
            "📌 الحد الأدنى: "
            + str(MIN_WITHDRAW)
            + " BNB\n\n"
            "أرسل مبلغ السحب.\n\n"
            "مثال:\n"
            "0.01"
        )


# =========================================================
# إدخال بيانات السحب
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if "withdraw_step" not in context.user_data:

        return

    user_id = update.effective_user.id

    step = context.user_data[
        "withdraw_step"
    ]

    text = update.message.text.strip()

    # -----------------------------------------------------
    # المبلغ
    # -----------------------------------------------------

    if step == "amount":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ أدخل مبلغاً صحيحاً.\n\n"
                "مثال:\n"
                "0.01"
            )

            return

        if amount <= 0:

            await update.message.reply_text(
                "❌ المبلغ يجب أن يكون أكبر من صفر."
            )

            return

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                "❌ الحد الأدنى للسحب هو "
                + str(MIN_WITHDRAW)
                + " BNB."
            )

            return

        balance = update_mining(
            user_id
        )

        if amount + WITHDRAW_FEE > balance:

            await update.message.reply_text(
                "❌ رصيدك غير كافٍ.\n\n"
                "رصيدك: "
                + str(round(balance, 8))
                + " BNB"
            )

            return

        context.user_data[
            "withdraw_amount"
        ] = amount

        context.user_data[
            "withdraw_step"
        ] = "wallet"

        await update.message.reply_text(
            "💳 الآن أرسل عنوان إيداع Binance.\n\n"
            "⚠️ اختر في Binance:\n"
            "BNB → Deposit → BNB Smart Chain (BEP20)\n\n"
            "يجب أن يبدأ العنوان بـ 0x"
        )

        return

    # -----------------------------------------------------
    # العنوان
    # -----------------------------------------------------

    if step == "wallet":

        wallet = text

        if not is_valid_bnb_address(
            wallet
        ):

            await update.message.reply_text(
                "❌ عنوان BNB غير صحيح.\n\n"
                "يجب أن يكون عنواناً يبدأ بـ 0x وطوله 42 حرفاً."
            )

            return

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if amount is None:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ انتهت جلسة السحب."
            )

            return

        # إنشاء الطلب وحجز الرصيد
        success, result = create_withdrawal(
            user_id,
            update.effective_user.username or "",
            amount,
            wallet
        )

        if not success:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ لم يتم إنشاء السحب.\n\n"
                + str(result)
            )

            return

        withdrawal_id = result

        context.user_data.clear()

        await update.message.reply_text(
            "⏳ تم إنشاء طلب السحب.\n\n"
            "🆔 الطلب: #"
            + str(withdrawal_id)
            + "\n"
            "💰 المبلغ: "
            + str(amount)
            + " BNB\n\n"
            "⚡ جاري إرسال BNB..."
        )

        # معالجة الإرسال
        success, txid_or_error = process_withdrawal(
            withdrawal_id
        )

        if success:

            txid = txid_or_error

            await update.message.reply_text(
                "✅ تم إرسال BNB بنجاح!\n\n"
                "💰 المبلغ: "
                + str(amount)
                + " BNB\n\n"
                "🔗 TXID:\n"
                + txid
                + "\n\n"
                "يمكنك متابعة المعاملة على BSCScan."
            )

            # إشعار الأدمن
            if ADMIN_ID != 123456789:

                try:

                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            "✅ سحب مكتمل\n\n"
                            "🆔 #"
                            + str(withdrawal_id)
                            + "\n"
                            "👤 "
                            + str(user_id)
                            + "\n"
                            "💰 "
                            + str(amount)
                            + " BNB\n"
                            "🔗 TXID:\n"
                            + txid
                        )
                    )

                except Exception as e:

                    print(e)

        else:

            await update.message.reply_text(
                "❌ فشل إرسال BNB.\n\n"
                "تمت إعادة المبلغ إلى رصيدك.\n\n"
                "سبب الخطأ:\n"
                + str(txid_or_error)
            )


# =========================================================
# أمر الأدمن
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

    wallet_balance = (
        get_hot_wallet_balance()
    )

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
            txid
        FROM withdrawals
        ORDER BY id DESC
        LIMIT 20
        """
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    text = (
        "👑 لوحة الأدمن\n\n"
        "💰 رصيد محفظة البوت:\n"
        + str(round(wallet_balance, 8))
        + " BNB\n\n"
    )

    if not rows:

        text += "📭 لا توجد طلبات سحب."

    else:

        text += "📋 آخر السحوبات:\n\n"

        for row in rows:

            text += (
                "🆔 #"
                + str(row[0])
                + "\n"
                "👤 "
                + str(row[1])
                + "\n"
                "💰 "
                + str(row[2])
                + " BNB\n"
                "📌 "
                + str(row[4])
                + "\n"
            )

            if row[5]:

                text += (
                    "🔗 "
                    + str(row[5])
                    + "\n"
                )

            text += "\n"

    await update.message.reply_text(
        text
    )


# =========================================================
# التشغيل
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

        print(
            "PostgreSQL connected"
        )

    except Exception as e:

        print(
            "Database Error:"
        )

        print(e)

        return

    # تهيئة BSC
    blockchain_ready = (
        init_blockchain()
    )

    if not blockchain_ready:

        print(
            "WARNING: السحب الحقيقي غير جاهز."
        )

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

    # لوحة الأدمن
    application.add_handler(
        CommandHandler(
            "withdrawals",
            admin_command
        )
    )

    # أزرار المستخدم
    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # الرسائل النصية للسحب
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    print(
        "BNB Mining Bot Started"
    )

    application.run_polling()


if __name__ == "__main__":

    main()
