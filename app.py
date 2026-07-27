import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv


BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(value) for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip().isdigit()}

dp = Dispatcher()
db = sqlite3.connect(BASE_DIR / "bot.db")
db.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    referral_code TEXT UNIQUE,
    invited_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)""")
db.execute("""CREATE TABLE IF NOT EXISTS support_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)""")
db.commit()


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
         InlineKeyboardButton(text="🎁 Промокоды", callback_data="promos")],
        [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral"),
         InlineKeyboardButton(text="🗂 Каталог", callback_data="catalog")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
         InlineKeyboardButton(text="ℹ️ Правила", callback_data="rules")],
    ])
def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💳 Пополнить"),
                KeyboardButton(text="💸 Вывести"),
            ],
            [
                KeyboardButton(text="👨‍💻 Поддержка"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← В меню", callback_data="home")]])


def ensure_user(user) -> str:
    code = f"U{user.id}"
    db.execute(
        "INSERT OR IGNORE INTO users(user_id, username, full_name, referral_code) VALUES (?, ?, ?, ?)",
        (user.id, user.username or "", user.full_name, code),
    )
    db.commit()
    return code


@dp.message(CommandStart())
async def start(message: Message):
    code = ensure_user(message.from_user)
    args = (message.text or "").split(maxsplit=1)
    if len(args) == 2 and args[1].startswith("ref_"):
        inviter = args[1][4:]
        if inviter != code:
            db.execute("UPDATE users SET invited_by = COALESCE(invited_by, ?) WHERE user_id = ?", (inviter, message.from_user.id))
            db.commit()
    await message.answer(
        "Добро пожаловать! Это независимый сервис с поддержкой, промокодами и программой приглашений.",
        reply_markup=menu(),
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню", reply_markup=menu())
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    code = ensure_user(callback.from_user)
    row = db.execute("SELECT COUNT(*) FROM users WHERE invited_by = ?", (code,)).fetchone()
    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\nИмя: {callback.from_user.full_name}\nВаш код: <code>{code}</code>\nПриглашено друзей: {row[0]}",
        reply_markup=back(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "referral")
async def referral(callback: CallbackQuery):
    code = ensure_user(callback.from_user)
    username = (await callback.bot.get_me()).username
    link = f"https://t.me/{username}?start=ref_{code}"
    await callback.message.edit_text(
        f"🤝 <b>Пригласить друга</b>\n\nВаша персональная ссылка:\n<code>{link}</code>\n\nБонусы и условия задаются администратором.",
        reply_markup=back(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "promos")
async def promos(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎁 <b>Промокоды</b>\n\nЗдесь можно публиковать акции, бонусы и правила их использования. Реальные финансовые операции в этом шаблоне отсутствуют.",
        reply_markup=back(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "catalog")
async def catalog(callback: CallbackQuery):
    await callback.message.edit_text(
        "🗂 <b>Каталог</b>\n\nРаздел для ваших продуктов, услуг или информационных карточек. Добавьте позиции из своей админ-панели или базы данных.",
        reply_markup=back(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ <b>Правила</b>\n\nДобавьте свои условия использования, политику конфиденциальности и контакты компании.",
        reply_markup=back(), parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    await callback.message.edit_text("💬 Напишите одним сообщением свой вопрос — он будет передан в поддержку.", reply_markup=back())
    await callback.answer()


@dp.message(F.text)
async def support_message(message: Message):
    @dp.message(F.text == "💳 Пополнить")
async def deposit_start(message: Message):
    await message.answer(
        "💳 <b>Пополнение счёта</b>\n\n"
        "Введите номер счёта, с которого вносите средства "
        "(DEPOSIT ID).\n\n"
        "Пример: <code>1749177745</code>",
        parse_mode="HTML",
    )


@dp.message(F.text == "💸 Вывести")
async def withdraw_start(message: Message):
    await message.answer(
        "💸 <b>Вывод средств</b>\n\n"
        "Введите номер вашего счёта для вывода.",
        parse_mode="HTML",
    )


@dp.message(F.text == "👨‍💻 Поддержка")
async def support_start(message: Message):
    await message.answer(
        "👨‍💻 <b>Поддержка</b>\n\n"
        "Напишите одним сообщением свой вопрос.\n"
        "После этого он будет передан оператору.",
        parse_mode="HTML",
    )
    ensure_user(message.from_user)
    db.execute("INSERT INTO support_requests(user_id, text) VALUES (?, ?)", (message.from_user.id, message.text))
    db.commit()
    for admin_id in ADMIN_IDS:
        await message.bot.send_message(admin_id, f"Новая заявка #{db.execute('SELECT last_insert_rowid()').fetchone()[0]}\nОт: {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n\n{message.text}")
    await message.answer(
    "│ Привет, <b>BANKTRANSFER</b> ! ❞\n\n"
    "<b>Пополнение и выводы 🇰🇬</b>\n\n"
    "│ 💸 0% комиссии ❞\n\n"
    "│ 🛡 Защищенные транзакции ❞\n\n"
    "│ 🚀 Обработка: 10 сек - 1 мин ❞\n\n"
    "│ 📩 Служба поддержки: "
    "<a href='https://t.me/ВАШ_USERNAME'>@ВАШ_USERNAME</a> ❞\n\n"
    "<b>Работаем 24/7! 💯</b>",
    parse_mode="HTML",
    reply_markup=main_keyboard(),
    disable_web_page_preview=True,
)


async def main():
    if not TOKEN or TOKEN == "put_your_token_here":
        raise RuntimeError("Укажите BOT_TOKEN в файле .env (см. .env.example).")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
