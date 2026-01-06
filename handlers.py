from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from datetime import datetime, timedelta
import logging

from keyboards import *
from database import Database

logger = logging.getLogger(__name__)

router = Router()

# =====================================================
# START / MENU
# =====================================================

@router.message(Command("start"))
async def start_cmd(message: Message, db: Database):
    user = await db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name
    )

    await message.answer(
        "👋 Добро пожаловать в бот автопостинга!\n\n"
        "Используй меню ниже 👇",
        reply_markup=main_menu_kb()
    )


@router.message(F.text == "🔄 Обновить")
async def refresh(message: Message):
    await message.answer("♻️ Обновлено", reply_markup=main_menu_kb())


# =====================================================
# TARIFFS (USER)
# =====================================================

@router.message(F.text == "💎 Тарифы и подписка")
async def show_tariffs(message: Message, db: Database):
    tariffs = await db.pool.fetch(
        "SELECT * FROM tariffs WHERE is_active = TRUE ORDER BY stars_price"
    )
    tariffs = [dict(t) for t in tariffs]

    await message.answer(
        "💎 <b>Доступные тарифы</b>",
        reply_markup=tariffs_kb(tariffs)
    )


@router.callback_query(F.data.startswith("buy_tariff:"))
async def buy_tariff(callback: CallbackQuery, db: Database):
    tariff = callback.data.split(":")[1]

    channel = await db.pool.fetchrow(
        "SELECT * FROM tariff_channels WHERE tariff_code=$1", tariff
    )

    if not channel:
        await callback.answer("❌ Канал не настроен", show_alert=True)
        return

    await callback.message.edit_text(
        f"🔐 Для активации тарифа <b>{tariff}</b>\n"
        "нужно вступить в приватный канал 👇",
        reply_markup=tariff_channel_join_kb(channel["invite_link"])
    )


@router.callback_query(F.data == "check_channel_join")
async def check_channel(callback: CallbackQuery):
    await callback.answer(
        "⏳ Проверка участия происходит автоматически\n"
        "Каждые 30 минут",
        show_alert=True
    )


# =====================================================
# CRYPTO PAYMENTS
# =====================================================

@router.callback_query(F.data.startswith("buy_crypto:"))
async def crypto_start(callback: CallbackQuery):
    tariff = callback.data.split(":")[1]

    await callback.message.edit_text(
        f"💰 <b>Crypto оплата ({tariff})</b>\n\n"
        "1️⃣ Создай чек в CryptoBot\n"
        "2️⃣ Отправь ID чека админу\n"
        "3️⃣ Нажми кнопку ниже",
        reply_markup=crypto_payment_kb(tariff)
    )


@router.callback_query(F.data.startswith("crypto_sent:"))
async def crypto_sent(callback: CallbackQuery, db: Database):
    tariff = callback.data.split(":")[1]

    user = await db.get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name
    )

    await db.pool.execute("""
        INSERT INTO crypto_payments (user_id, tariff_code, amount)
        VALUES ($1,$2,
            (SELECT crypto_price FROM tariffs WHERE code=$2)
        )
    """, user["id"], tariff)

    await callback.message.answer(
        "✅ Чек отправлен админу.\n"
        "Подписка активируется после подтверждения."
    )


# =====================================================
# ADMIN PANEL
# =====================================================

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not message.from_user.id:
        return
    await message.answer("⚙️ Админ-панель", reply_markup=admin_menu_kb())


@router.message(F.text == "⚙️ Тарифы")
async def admin_tariffs(message: Message, db: Database):
    tariffs = await db.pool.fetch("SELECT * FROM tariffs ORDER BY stars_price")
    tariffs = [dict(t) for t in tariffs]

    await message.answer(
        "⚙️ Управление тарифами",
        reply_markup=admin_tariffs_kb(tariffs)
    )


@router.callback_query(F.data.startswith("admin_tariff:"))
async def admin_tariff_manage(callback: CallbackQuery):
    code = callback.data.split(":")[1]

    await callback.message.edit_text(
        f"⚙️ Тариф <b>{code}</b>",
        reply_markup=admin_tariff_manage_kb(code)
    )


@router.callback_query(F.data.startswith("toggle_tariff:"))
async def toggle_tariff(callback: CallbackQuery, db: Database):
    code = callback.data.split(":")[1]

    await db.pool.execute("""
        UPDATE tariffs SET is_active = NOT is_active WHERE code=$1
    """, code)

    await callback.answer("🔁 Статус изменён", show_alert=True)


# =====================================================
# ADMIN – TARIFF CHANNELS
# =====================================================

@router.message(F.text == "🔐 Каналы тарифов")
async def admin_tariff_channels(message: Message, db: Database):
    tariffs = await db.pool.fetch("SELECT code,name FROM tariffs")
    tariffs = [dict(t) for t in tariffs]

    await message.answer(
        "🔐 Приватные каналы тарифов",
        reply_markup=admin_tariff_channels_kb(tariffs)
    )


@router.callback_query(F.data.startswith("set_tariff_channel:"))
async def set_tariff_channel(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    await callback.message.answer(
        f"📨 Пришли ID канала и invite-ссылку\n"
        f"Формат:\n<code>-1001234567890 https://t.me/+xxxx</code>\n\n"
        f"Тариф: {code}"
    )


# =====================================================
# ADMIN – CRYPTO PAYMENTS
# =====================================================

@router.message(F.text == "💳 Крипто-платежи")
async def admin_crypto(message: Message, db: Database):
    payments = await db.pool.fetch("""
        SELECT cp.*, u.telegram_id
        FROM crypto_payments cp
        JOIN users u ON u.id = cp.user_id
        WHERE confirmed = FALSE
    """)

    payments = [dict(p) for p in payments]

    if not payments:
        await message.answer("Нет ожидающих платежей")
        return

    await message.answer(
        "💳 Ожидают подтверждения",
        reply_markup=admin_crypto_payments_kb(payments)
    )


@router.callback_query(F.data.startswith("crypto_payment:"))
async def admin_crypto_action(callback: CallbackQuery):
    pid = int(callback.data.split(":")[1])

    await callback.message.edit_text(
        f"Подтвердить платёж #{pid}?",
        reply_markup=admin_crypto_action_kb(pid)
    )


@router.callback_query(F.data.startswith("crypto_approve:"))
async def crypto_approve(callback: CallbackQuery, db: Database):
    pid = int(callback.data.split(":")[1])

    payment = await db.pool.fetchrow(
        "SELECT * FROM crypto_payments WHERE id=$1", pid
    )

    await db.set_tariff(payment["user_id"], payment["tariff_code"])
    await db.pool.execute(
        "UPDATE crypto_payments SET confirmed=TRUE WHERE id=$1", pid
    )

    await callback.answer("✅ Подписка активирована", show_alert=True)
