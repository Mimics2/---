from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =====================================================
# USER KEYBOARDS
# =====================================================

def main_menu_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="📊 Моя статистика")],
            [KeyboardButton(text="📢 Мои каналы"), KeyboardButton(text="➕ Добавить канал")],
            [KeyboardButton(text="📝 Создать пост"), KeyboardButton(text="📅 Мои посты")],
            [KeyboardButton(text="💎 Тарифы и подписка")],
            [KeyboardButton(text="🔄 Обновить")]
        ]
    )


def tariffs_kb(tariffs: list):
    """
    tariffs = [{code,name,stars_price,crypto_price}]
    """
    kb = InlineKeyboardBuilder()

    for t in tariffs:
        text = f"{t['name']} — {t['stars_price']}⭐"
        kb.button(
            text=text,
            callback_data=f"buy_tariff:{t['code']}"
        )

        if t['crypto_price']:
            kb.button(
                text=f"{t['name']} (Crypto ${t['crypto_price']})",
                callback_data=f"buy_crypto:{t['code']}"
            )

    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back"))
    return kb.as_markup()


def tariff_channel_join_kb(invite_link: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Вступить в приватный канал", url=invite_link)],
            [InlineKeyboardButton(text="✅ Я вступил", callback_data="check_channel_join")]
        ]
    )


def crypto_payment_kb(tariff_code: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤖 Создать чек в CryptoBot",
                    url="https://t.me/CryptoBot?start=create"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Я отправил чек админу",
                    callback_data=f"crypto_sent:{tariff_code}"
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ]
    )


def confirm_kb(action: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{action}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
            ]
        ]
    )


# =====================================================
# ADMIN KEYBOARDS
# =====================================================

def admin_menu_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="📊 Общая статистика")],
            [KeyboardButton(text="⚙️ Тарифы"), KeyboardButton(text="🔐 Каналы тарифов")],
            [KeyboardButton(text="💰 Цены"), KeyboardButton(text="👥 Пользователи")],
            [KeyboardButton(text="💳 Крипто-платежи")],
            [KeyboardButton(text="🏠 Выйти")]
        ]
    )


def admin_tariffs_kb(tariffs: list):
    kb = InlineKeyboardBuilder()
    for t in tariffs:
        kb.button(
            text=f"{t['name']} ({'ON' if t['is_active'] else 'OFF'})",
            callback_data=f"admin_tariff:{t['code']}"
        )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return kb.as_markup()


def admin_tariff_manage_kb(code: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить лимиты",
                    callback_data=f"edit_limits:{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Изменить цены",
                    callback_data=f"edit_prices:{code}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Вкл / Выкл",
                    callback_data=f"toggle_tariff:{code}"
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_tariffs")]
        ]
    )


def admin_tariff_channels_kb(tariffs: list):
    kb = InlineKeyboardBuilder()
    for t in tariffs:
        kb.button(
            text=f"🔐 {t['name']}",
            callback_data=f"set_tariff_channel:{t['code']}"
        )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return kb.as_markup()


def admin_crypto_payments_kb(payments: list):
    kb = InlineKeyboardBuilder()
    for p in payments:
        kb.button(
            text=f"#{p['id']} | {p['tariff_code']} | ${p['amount']}",
            callback_data=f"crypto_payment:{p['id']}"
        )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return kb.as_markup()


def admin_crypto_action_kb(payment_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"crypto_approve:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"crypto_reject:{payment_id}"
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_crypto")]
        ]
    )
