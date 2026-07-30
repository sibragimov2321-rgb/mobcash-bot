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
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


router = Router()
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ACCOUNT_GUIDE_IMAGE = (
    ASSETS_DIR / "account_guide.jpg"
    if (ASSETS_DIR / "account_guide.jpg").exists()
    else BASE_DIR / "account_guide.jpg"
)
UNIVERSAL_QR_IMAGE = (
    ASSETS_DIR / "qr_omoney.jpg"
    if (ASSETS_DIR / "qr_omoney.jpg").exists()
    else BASE_DIR / "qr_omoney.jpg"
)
DEFAULT_BANK_URLS = {
    "MBANK": "https://app.mbank.kg/qr/#00020101021132440012c2c.mbank.kg01020210129965555139111302125204999953034175911ZhASULAN%20T.63044af1",
    "O!Деньги": "https://api.dengi.o.kg/#00020101021132680012p2p.dengi.kg01048580111233693544705710129965555139111202111302123410%D0%96%D0%B0%D1%81%D1%83%D0%BB%D0%B0%D0%BD%20%D0%A2.520473995303417540105906O%21Bank6304D082",
}
DEFAULT_PARTNER_URL = "https://t.me/MelBetmell"


@dataclass(frozen=True)
class Settings:
    token: str
    support: str
    partner_url: str
    required_channel: str
    required_channel_url: str
    admin_ids: set[int]
    payment_timeout: int
    min_deposit: int
    max_deposit: int
    bank_urls: dict[str, str]


def parse_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")
    support = os.getenv("SUPPORT_USERNAME", "@support").strip()
    partner_url = os.getenv("PARTNER_URL", "").strip()
    if not partner_url:
        partner_url = DEFAULT_PARTNER_URL
    banks = {
        "MBANK": os.getenv("PAYMENT_MBANK_URL", "").strip()
        or DEFAULT_BANK_URLS["MBANK"],
        "O!Деньги": os.getenv("PAYMENT_OMONEY_URL", "").strip()
        or DEFAULT_BANK_URLS["O!Деньги"],
    }
    return Settings(
        token=token,
        support=support,
        partner_url=partner_url,
        required_channel=os.getenv("REQUIRED_CHANNEL", "").strip(),
        required_channel_url=os.getenv("REQUIRED_CHANNEL_URL", "").strip(),
        admin_ids=parse_ids(os.getenv("ADMIN_IDS", "")),
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                telegram_id INTEGER PRIMARY KEY
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS banned_users (
                telegram_id INTEGER PRIMARY KEY
            )
            """
        )


def save_user(message: Message) -> None:
    if message.from_user is None:
        return
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO users (telegram_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
            """,
            (
                message.from_user.id,
                message.from_user.username or "",
                message.from_user.full_name,
            ),
        )


def create_request(telegram_id: int, platform: str, account_id: str, amount: str, bank: str) -> int:
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "INSERT INTO requests (telegram_id, platform, account_id, amount, bank) VALUES (?, ?, ?, ?, ?)",
            (telegram_id, platform, account_id, amount, bank),
        )
        return int(cursor.lastrowid)


def get_setting(key: str, default: str = "") -> str:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT value FROM bot_settings WHERE key = ?",
            (key,),
        ).fetchone()
    return str(row[0]) if row else default


def set_setting(key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO bot_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def is_admin(user_id: int) -> bool:
    if user_id in settings.admin_ids:
        return True
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT 1 FROM admins WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
    return row is not None


def add_admin(user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)",
            (user_id,),
        )


def remove_admin(user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DELETE FROM admins WHERE telegram_id = ?", (user_id,))


def is_banned(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT 1 FROM banned_users WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
    return row is not None


def set_ban(user_id: int, banned: bool) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        if banned:
            connection.execute(
                "INSERT OR IGNORE INTO banned_users (telegram_id) VALUES (?)",
                (user_id,),
            )
        else:
            connection.execute("DELETE FROM banned_users WHERE telegram_id = ?", (user_id,))


def admin_stats() -> tuple[int, int, int, int]:
    with sqlite3.connect(DB_PATH) as connection:
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        requests = connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        receipts = connection.execute(
            "SELECT COUNT(*) FROM requests WHERE status = 'waiting_receipt'"
        ).fetchone()[0]
        banned = connection.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
    return int(users), int(requests), int(receipts), int(banned)


def all_user_ids() -> list[int]:
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute("SELECT telegram_id FROM users").fetchall()
    return [int(row[0]) for row in rows]


class Deposit(StatesGroup):
    platform = State()
    account_id = State()
    amount = State()
    receipt = State()


class AdminState(StatesGroup):
    add_admin = State()
    remove_admin = State()
    ban = State()
    unban = State()
    broadcast = State()


MAIN_DEPOSIT = "⬇️ Пополнить"
MAIN_WITHDRAW = "⬆️ Вывести"
MAIN_REFERRAL = "🤝 Пригласи друга"
CANCEL = "✖️ Отмена"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_DEPOSIT), KeyboardButton(text=MAIN_WITHDRAW)],
            [KeyboardButton(text=MAIN_REFERRAL)],
        ],
        resize_keyboard=True,
    )


def platforms_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="MELBET")],
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="MBANK ↗", url=settings.bank_urls["MBANK"]),
                InlineKeyboardButton(text="O!Деньги ↗", url=settings.bank_urls["O!Деньги"]),
            ],
            [InlineKeyboardButton(text=CANCEL, callback_data="cancel")],
        ]
    )


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if settings.required_channel_url:
        rows.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=settings.required_channel_url)])
    rows.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="subscription:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_keyboard() -> InlineKeyboardMarkup:
    username = settings.support.lstrip("@").strip()
    if not username:
        return InlineKeyboardMarkup(inline_keyboard=[])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ НАПИСАТЬ В ПОДДЕРЖКУ", url=f"https://t.me/{username}")]
        ]
    )


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


def admin_keyboard() -> InlineKeyboardMarkup:
    disabled = get_setting("maintenance", "0") == "1"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="🔗 Настроить ссылки", callback_data="admin:links")],
            [
                InlineKeyboardButton(text="✈️ Рассылка", callback_data="admin:broadcast"),
                InlineKeyboardButton(text="🔗 Пост в канал", callback_data="admin:channel_post"),
            ],
            [
                InlineKeyboardButton(text="🎁 Счастливый час", callback_data="admin:happy_hour"),
                InlineKeyboardButton(text="📊 История игрока", callback_data="admin:player_history"),
            ],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings")],
            [
                InlineKeyboardButton(
                    text="🛡️ Включить бота" if disabled else "🛡️ Выключить бота",
                    callback_data="admin:toggle",
                )
            ],
            [
                InlineKeyboardButton(text="🛡️ Назначить админа", callback_data="admin:add_admin"),
                InlineKeyboardButton(text="⚠️ Снять админа", callback_data="admin:remove_admin"),
            ],
            [
                InlineKeyboardButton(text="🔒 Забанить", callback_data="admin:ban"),
                InlineKeyboardButton(text="🔓 Разбанить", callback_data="admin:unban"),
            ],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin:close")],
        ]
    )


def admin_home_text() -> str:
    disabled = get_setting("maintenance", "0") == "1"
    return (
        "Добро пожаловать в Админ-панель:\n\n"
        f"Статус бота: {'🔒' if disabled else '📥'} "
        f"<b>{'ВЫКЛЮЧЕН' if disabled else 'ВКЛЮЧЕН'}</b>"
    )


async def show_admin(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("Админ-панель доступна только администратору.")
        return
    await message.answer(admin_home_text(), reply_markup=admin_keyboard())


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
    save_user(message)
    if is_banned(message.from_user.id):
        await message.answer("Доступ к боту закрыт.")
        return False
    if get_setting("maintenance", "0") == "1" and not is_admin(message.from_user.id):
        await message.answer("В системе ведутся технические работы. Пожалуйста, зайдите позже.")
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
    support_link = f"https://t.me/{settings.support.lstrip('@')}"
    await message.answer(
        f"<blockquote>Привет, {name}! 💬</blockquote>\n\n"
        "<b>Пополнение и выводы 🇰🇬</b>\n\n"
        "<blockquote>✂️ 0% комиссии 💬</blockquote>\n"
        "<blockquote>🛡️ Защищенные транзакции 💬</blockquote>\n"
        "<blockquote>🛫 Обработка: 10 сек - 1 мин 💬</blockquote>\n"
        f"<blockquote>📋 Служба поддержки: <a href=\"{support_link}\">{support}</a> 💬</blockquote>\n\n"
        "<b>Работаем 24/7! 💯</b>\n\n"
        "Выберите действие 👇",
        disable_web_page_preview=True,
        reply_markup=support_keyboard(),
    )
    await message.answer(
        "Выберите действие кнопками ниже.",
        reply_markup=main_keyboard(),
    )


@router.message(CommandStart())
async def start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    save_user(message)
    if message.from_user and not await subscribed(bot, message.from_user.id):
        await message.answer(
            "<b>Что умеет этот бот?</b>\n\n"
            "БЫСТРЫЕ ПОПОЛНЕНИЕ И ВЫВОДЫ 💸\n\n"
            "С НУЛЕВЫМ ПРОЦЕНТОМ КОМИССИИ И БЕЗОПАСНОСТЬ ВАШЕГО ДЕПОЗИТА 🏆\n\n"
            "ВЫВОД 0% ⚡\nПОПОЛНЕНИЕ 0% ⚡ ОБСЛУЖИВАНИЕ ОТ ОДНОЙ СЕКУНДЫ ДО ПЯТИ МИНУТ ⚡\n\n"
            f"СЛУЖБА ПОДДЕРЖКИ {html.escape(settings.support)}"
        )
        await allow(message, bot)
        return
    if await allow(message, bot):
        await show_home(message)


@router.message(Command("id"))
async def id_command(message: Message) -> None:
    if message.from_user:
        save_user(message)
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_admin(message)


@router.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    action = (callback.data or "").split(":", 1)[1]
    message = callback.message
    if message is None:
        await callback.answer()
        return

    if action == "stats":
        users, requests, receipts, banned = admin_stats()
        await callback.answer()
        await message.answer(
            "<b>📊 Статистика</b>\n\n"
            f"Пользователей: <b>{users}</b>\n"
            f"Заявок: <b>{requests}</b>\n"
            f"Ожидают чек: <b>{receipts}</b>\n"
            f"В бане: <b>{banned}</b>",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "toggle":
        new_value = "0" if get_setting("maintenance", "0") == "1" else "1"
        set_setting("maintenance", new_value)
        await callback.answer("Статус изменён")
        await message.edit_text(admin_home_text(), reply_markup=admin_keyboard())
        return

    if action == "add_admin":
        await state.set_state(AdminState.add_admin)
        await callback.answer()
        await message.answer("Отправьте Telegram ID нового админа.")
        return

    if action == "remove_admin":
        await state.set_state(AdminState.remove_admin)
        await callback.answer()
        await message.answer("Отправьте Telegram ID админа, которого нужно снять.")
        return

    if action == "ban":
        await state.set_state(AdminState.ban)
        await callback.answer()
        await message.answer("Отправьте Telegram ID игрока для бана.")
        return

    if action == "unban":
        await state.set_state(AdminState.unban)
        await callback.answer()
        await message.answer("Отправьте Telegram ID игрока для разбана.")
        return

    if action == "broadcast":
        await state.set_state(AdminState.broadcast)
        await callback.answer()
        await message.answer("Отправьте текст рассылки. Он уйдёт всем пользователям бота.")
        return

    if action == "close":
        await callback.answer()
        await message.delete()
        return

    labels = {
        "links": "Настроить ссылки",
        "channel_post": "Пост в канал",
        "happy_hour": "Счастливый час",
        "player_history": "История игрока",
        "settings": "Настройки",
    }
    await callback.answer(f"Раздел «{labels.get(action, action)}» добавлен в панель.", show_alert=True)


def parse_admin_target(text: str | None) -> int | None:
    value = (text or "").strip()
    if not value.isdigit():
        return None
    return int(value)


@router.message(AdminState.add_admin)
async def add_admin_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    target = parse_admin_target(message.text)
    if target is None:
        await message.answer("Отправьте только цифры Telegram ID.")
        return
    add_admin(target)
    await state.clear()
    await message.answer(f"Админ назначен: <code>{target}</code>", reply_markup=admin_keyboard())


@router.message(AdminState.remove_admin)
async def remove_admin_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    target = parse_admin_target(message.text)
    if target is None:
        await message.answer("Отправьте только цифры Telegram ID.")
        return
    remove_admin(target)
    await state.clear()
    await message.answer(f"Админ снят: <code>{target}</code>", reply_markup=admin_keyboard())


@router.message(AdminState.ban)
async def ban_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    target = parse_admin_target(message.text)
    if target is None:
        await message.answer("Отправьте только цифры Telegram ID.")
        return
    set_ban(target, True)
    await state.clear()
    await message.answer(f"Игрок забанен: <code>{target}</code>", reply_markup=admin_keyboard())


@router.message(AdminState.unban)
async def unban_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    target = parse_admin_target(message.text)
    if target is None:
        await message.answer("Отправьте только цифры Telegram ID.")
        return
    set_ban(target, False)
    await state.clear()
    await message.answer(f"Игрок разбанен: <code>{target}</code>", reply_markup=admin_keyboard())


@router.message(AdminState.broadcast)
async def broadcast_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    text = message.text or ""
    if not text.strip():
        await message.answer("Пока поддерживается текстовая рассылка. Отправьте текст.")
        return
    sent = 0
    failed = 0
    for user_id in all_user_ids():
        try:
            await bot.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(
        f"Рассылка завершена.\n\nДоставлено: <b>{sent}</b>\nОшибок: <b>{failed}</b>",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "subscription:check")
async def check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        return
    if await subscribed(bot, callback.from_user.id):
        await callback.answer("Подписка подтверждена")
        await callback.message.answer("✅ Подписка подтверждена! Добро пожаловать.")
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


@router.message(Deposit.platform, F.text == "MELBET")
async def platform_selected(message: Message, state: FSMContext) -> None:
    await state.update_data(platform=message.text)
    await state.set_state(Deposit.account_id)
    text = (
        "💰 <b>Пополнение счета</b>\n\n"
        "Счет: <b>MELBET</b>\n\n"
        "Введите ID счета:"
    )
    await message.answer("💰 <b>Пополнение счета</b>\n\nВыберите счет:")
    if ACCOUNT_GUIDE_IMAGE.exists():
        await message.answer_photo(
            FSInputFile(ACCOUNT_GUIDE_IMAGE),
            caption=text,
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await message.answer(text, reply_markup=ReplyKeyboardRemove())


@router.message(Deposit.account_id)
async def account_id_entered(message: Message, state: FSMContext) -> None:
    account_id = (message.text or "").strip()
    if not account_id.isdigit() or not 5 <= len(account_id) <= 20:
        data = await state.get_data()
        await message.answer(f"ℹ️ Неверный ID для {html.escape(str(data.get('platform', 'платформы')))}")
        return
    await state.update_data(account_id=account_id)
    await state.set_state(Deposit.amount)
    await message.answer(
        "✅ ID принят.\n\nТеперь, пожалуйста, введите сумму:\n\n"
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
    data = await state.get_data()
    request_id = create_request(
        message.from_user.id if message.from_user else 0,
        str(data.get("platform", "MELBET")),
        str(data.get("account_id", "")),
        exact,
        "UNIVERSAL_QR",
    )
    caption = (
        f"✅ <b>Сумма к оплате: {exact} KGS</b>\n"
        f"🎮 MELBET ID: <code>{html.escape(str(data.get('account_id', '')))}</code>\n\n"
        "⚠️ Актуально в течение 5 минут.\n\n"
        "Обязательно переведите точную сумму с копейками.\n"
        "После оплаты отправьте чек в этот чат.\n\n"
        f"Заявка: <b>#{request_id}</b>"
    )
    if UNIVERSAL_QR_IMAGE.exists():
        await message.answer_photo(
            FSInputFile(UNIVERSAL_QR_IMAGE),
            caption=caption,
            reply_markup=bank_keyboard(),
        )
    else:
        await message.answer(caption, reply_markup=bank_keyboard())


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


@router.message(F.text == MAIN_REFERRAL)
async def referral(message: Message) -> None:
    await message.answer("🤝 Реферальная программа будет добавлена позже.", reply_markup=main_keyboard())


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
