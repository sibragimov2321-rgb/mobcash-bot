"""MOBCASH — самостоятельный Telegram-бот для заявок на пополнение.

Этот файл намеренно не использует код предыдущего бота. Все тексты, сценарии
и клавиатуры определены здесь заново.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


router = Router()


@dataclass(frozen=True)
class Settings:
    token: str
    support: str
    required_channel: str
    required_channel_url: str
    payment_timeout: int
    min_deposit: int
    max_deposit: int
    bank_urls: dict[str, str]


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")
    banks = {
        "MBANK": os.getenv("PAYMENT_MBANK_URL", "").strip(),
        "O!Деньги": os.getenv("PAYMENT_OMONEY_URL", "").strip(),
        "BAKAI": os.getenv("PAYMENT_BAKAI_BANK_URL", "").strip(),
        "MegaPay": os.getenv("PAYMENT_MEGAPAY_URL", "").strip(),
    }
    return Settings(
        token=token,
        support=os.getenv("SUPPORT_USERNAME", "@support").strip(),
        required_channel=os.getenv("REQUIRED_CHANNEL", "").strip(),
        required_channel_url=os.getenv("REQUIRED_CHANNEL_URL", "").strip(),
        payment_timeout=int(os.getenv("PAYMENT_TIMEOUT_SECONDS", "300")),
        min_deposit=int(os.getenv("MIN_DEPOSIT", "35")),
        max_deposit=int(os.getenv("MAX_DEPOSIT", "500000")),
        bank_urls=banks,
    )


settings = load_settings()
DB_PATH = Path(os.getenv("DATABASE_PATH", "mobcash.sqlite3"))


def init_database() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                bank TEXT,
                status TEXT NOT NULL DEFAULT 'waiting_receipt'
            )
            """
        )


def create_request(telegram_id: int, platform: str, account_id: str, amount: str, bank: str) -> int:
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "INSERT INTO requests (telegram_id, platform, account_id, amount, bank) VALUES (?, ?, ?, ?, ?)",
            (telegram_id, platform, account_id, amount, bank),
        )
        return int(cursor.lastrowid)


class Deposit(StatesGroup):
    platform = State()
    account_id = State()
    amount = State()
    receipt = State()


MAIN_DEPOSIT = "💳 Пополнить"
MAIN_WITHDRAW = "💸 Вывести"
MAIN_SUPPORT = "👨‍💼 Поддержка"
CANCEL = "✖️ Отмена"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_DEPOSIT), KeyboardButton(text=MAIN_WITHDRAW)],
            [KeyboardButton(text=MAIN_SUPPORT)],
        ],
        resize_keyboard=True,
    )


def platforms_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1XBET"), KeyboardButton(text="MELBET")],
            [KeyboardButton(text=CANCEL)],
        ],
        resize_keyboard=True,
    )


def amounts_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="100"), KeyboardButton(text="200"), KeyboardButton(text="500")],
            [KeyboardButton(text="1000"), KeyboardButton(text="2000"), KeyboardButton(text="5000")],
            [KeyboardButton(text="10000")],
            [KeyboardButton(text=CANCEL)],
        ],
        resize_keyboard=True,
    )


def bank_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    banks = ("MBANK", "O!Деньги", "BAKAI", "MegaPay")
    for first, second in ((banks[0], banks[1]), (banks[2], banks[3])):
        rows.append(
            [
                InlineKeyboardButton(text=f"{first} ↗", callback_data=f"bank:{first}"),
                InlineKeyboardButton(text=f"{second} ↗", callback_data=f"bank:{second}"),
            ]
        )
    rows.append([InlineKeyboardButton(text=CANCEL, callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if settings.required_channel_url:
        rows.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=settings.required_channel_url)])
    rows.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="subscription:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_link_keyboard(url: str) -> InlineKeyboardMarkup:
    if url.startswith("https://") or url.startswith("http://"):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📲 Android", url=url), InlineKeyboardButton(text="🍎 iPhone", url=url)],
                [InlineKeyboardButton(text=CANCEL, callback_data="cancel")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=CANCEL, callback_data="cancel")]]
    )


async def subscribed(bot: Bot, user_id: int) -> bool:
    if not settings.required_channel:
        return True
    try:
        member = await bot.get_chat_member(settings.required_channel, user_id)
    except Exception:
        logging.exception("Subscription check failed")
        return False
    return member.status in {"creator", "administrator", "member"}


async def allow(message: Message, bot: Bot) -> bool:
    if message.from_user is None:
        return False
    if await subscribed(bot, message.from_user.id):
        return True
    await message.answer(
        "<b>📢 Для использования MOBCASH подпишитесь на канал.</b>\n\n"
        "После подписки нажмите «Проверить подписку».",
        reply_markup=subscription_keyboard(),
    )
    return False


async def show_home(message: Message) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "друг")
    support = html.escape(settings.support)
    await message.answer(
        f"<b>MOBCASH</b>\n\n"
        f"Привет, {name}! 💬\n\n"
        "<b>Пополнение и выводы 🇰🇬</b>\n\n"
        "✂️ 0% комиссии\n"
        "🛡️ Защищённые транзакции\n"
        "🚀 Обработка: 10 сек – 1 мин\n\n"
        f"👨‍💼 Служба поддержки: {support}\n\n"
        "<b>Работаем 24/7! 💯</b>",
        reply_markup=main_keyboard(),
    )


@router.message(CommandStart())
async def start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    if await allow(message, bot):
        await show_home(message)


@router.callback_query(F.data == "subscription:check")
async def check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        return
    if await subscribed(bot, callback.from_user.id):
        await callback.answer("Подписка подтверждена")
        await show_home(callback.message)
    else:
        await callback.answer("Подписка пока не найдена", show_alert=True)


@router.message(F.text == MAIN_DEPOSIT)
async def deposit_start(message: Message, bot: Bot, state: FSMContext) -> None:
    if not await allow(message, bot):
        return
    await state.set_state(Deposit.platform)
    await message.answer(
        "<b>💳 Пополнение</b>\n\nВыберите игровую платформу:",
        reply_markup=platforms_keyboard(),
    )


@router.message(Deposit.platform, F.text.in_({"1XBET", "MELBET"}))
async def platform_selected(message: Message, state: FSMContext) -> None:
    await state.update_data(platform=message.text)
    await state.set_state(Deposit.account_id)
    await message.answer(
        "<b>ПОПОЛНЕНИЕ СЧЁТА</b>\n\n"
        "Введите номер счёта, с которого вносите средства\n"
        "<b>(DEPOSIT ID)</b>",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Deposit.account_id)
async def account_id_entered(message: Message, state: FSMContext) -> None:
    account_id = (message.text or "").strip()
    if not account_id.isdigit() or not 5 <= len(account_id) <= 20:
        await message.answer("⚠️ Введите корректный цифровой Deposit ID.")
        return
    await state.update_data(account_id=account_id)
    await state.set_state(Deposit.amount)
    await message.answer(
        "Теперь, пожалуйста, введите сумму:\n\n"
        f"Минимум: <b>{settings.min_deposit} KGS</b>\n"
        f"Максимум: <b>{settings.max_deposit:,} KGS</b>",
        reply_markup=amounts_keyboard(),
    )


def parse_amount(text: str) -> int | None:
    cleaned = text.replace(" ", "").replace(",", ".")
    try:
        value = int(float(cleaned))
    except ValueError:
        return None
    return value


@router.message(Deposit.amount)
async def amount_entered(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None or not settings.min_deposit <= amount <= settings.max_deposit:
        await message.answer(
            f"⚠️ Введите сумму от {settings.min_deposit} до {settings.max_deposit:,} KGS."
        )
        return
    # Уникальные тыйыны позволяют сопоставить банковский перевод с заявкой.
    tyiyn = random.randint(1, 99)
    exact = f"{amount}.{tyiyn:02d}"
    await state.update_data(amount=amount, exact=exact)
    await state.set_state(Deposit.receipt)
    await message.answer(
        f"✅ <b>Сумма к оплате: {exact} KGS</b>\n"
        "⚠️ Актуально в течение 5 минут.\n\n"
        "Обязательно переведите точную сумму с копейками.\n"
        "После оплаты отправьте чек в этот чат.",
        reply_markup=bank_keyboard(),
    )


@router.callback_query(F.data.startswith("bank:"))
async def bank_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.from_user is None:
        return
    data = await state.get_data()
    if not data.get("exact"):
        await callback.answer("Начните пополнение заново", show_alert=True)
        return
    bank = callback.data.split(":", 1)[1]
    request_id = create_request(
        callback.from_user.id,
        str(data["platform"]),
        str(data["account_id"]),
        str(data["exact"]),
        bank,
    )
    url = settings.bank_urls.get(bank, "")
    text = (
        f"<b>🏦 {html.escape(bank)}</b>\n\n"
        f"Платформа: <b>{html.escape(str(data['platform']))}</b>\n"
        f"Игровой ID: <code>{html.escape(str(data['account_id']))}</code>\n"
        f"✅ Сумма к оплате: <b>{data['exact']} KGS</b>\n\n"
        "⚠️ Переведите точную сумму с копейками.\n"
        "📎 После оплаты отправьте фото или файл чека в этот чат."
    )
    if not url:
        text += "\n\nℹ️ Прямая ссылка банка ещё не добавлена администратором."
    await callback.message.answer(text, reply_markup=payment_link_keyboard(url))
    await callback.answer(f"Заявка #{request_id} создана")


@router.message(Deposit.receipt, F.photo | F.document)
async def receipt_received(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await message.answer(
        "✅ <b>Чек получен.</b>\n\n"
        f"Сумма: <b>{html.escape(str(data.get('exact', '—')))} KGS</b>\n"
        "Заявка передана на проверку. После подтверждения баланс будет пополнен.",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == MAIN_WITHDRAW)
async def withdrawal(message: Message, bot: Bot) -> None:
    if not await allow(message, bot):
        return
    await message.answer(
        "<b>💸 Вывод средств</b>\n\n"
        "Для вывода напишите в службу поддержки: "
        f"{html.escape(settings.support)}",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == MAIN_SUPPORT)
async def support(message: Message) -> None:
    await message.answer(f"👨‍💼 Поддержка: {html.escape(settings.support)}")


@router.message(F.text == CANCEL)
@router.callback_query(F.data == "cancel")
async def cancel(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    message = event.message if isinstance(event, CallbackQuery) else event
    if message is None:
        return
    await message.answer("Действие отменено.", reply_markup=main_keyboard())
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Выберите действие кнопками меню или отправьте /start.", reply_markup=main_keyboard())


async def main() -> None:
    init_database()
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    asyncio.run(main())
