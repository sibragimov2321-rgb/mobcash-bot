from __future__ import annotations

import asyncio
import html
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)

from config import Settings
from database import Card, Database


admin_router = Router(name="admin-panel")
database: Database
settings: Settings


ROLE_LABELS = {
    "owner": "Владелец",
    "supervisor": "Управляющий",
    "manager": "Менеджер",
}

STATUS_LABELS = {
    "active": "🟢 Активна",
    "reserve": "🟡 Резерв",
    "disabled": "🔴 Отключена",
    "limit_reached": "⚫ Лимит исчерпан",
}

DIRECTION_LABELS = {
    "deposit": "Ввод",
    "withdrawal": "Вывод",
    "both": "Ввод и вывод",
}


class AdminStates(StatesGroup):
    card_number = State()
    card_holder = State()
    card_daily_limit = State()
    card_single_limit = State()
    card_limit_value = State()
    team_identifier = State()
    broadcast_content = State()
    system_value = State()


def configure_admin_panel(db: Database, app_settings: Settings) -> None:
    global database, settings
    database = db
    settings = app_settings


def format_money(amount_minor: int) -> str:
    if amount_minor % 100 == 0:
        amount = f"{amount_minor // 100:,}".replace(",", " ")
    else:
        amount = f"{Decimal(amount_minor) / Decimal(100):,.2f}".replace(",", " ")
    return f"{amount} {settings.currency}"


def parse_amount(text: str, allow_zero: bool = True) -> int | None:
    normalized = text.strip().replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", normalized):
        return None
    try:
        value = Decimal(normalized).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        return None
    if value < 0 or (not allow_zero and value == 0):
        return None
    return int(value * 100)


def mask_card_number(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    if len(compact) <= 4:
        return f"•••• {html.escape(compact)}"
    if len(compact) <= 8:
        return f"•••• {html.escape(compact[-4:])}"
    return f"{html.escape(compact[:4])} •••• •••• {html.escape(compact[-4:])}"


def back_keyboard(callback_data: str = "ap:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
        ]
    )


def role_of(user_id: int) -> str | None:
    return database.get_staff_role(user_id)


async def audit_event(
    bot: Bot,
    actor: User,
    action: str,
    *,
    title: str = "🧭 ДЕЙСТВИЕ В АДМИН-ПАНЕЛИ",
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: str = "",
) -> None:
    role = role_of(actor.id)
    if role is None:
        return
    database.add_audit_log(
        actor.id,
        role,
        action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or None,
    )
    log_chat_id_raw = database.get_setting("log_chat_id", "").strip()
    if not log_chat_id_raw:
        return
    try:
        log_chat_id = int(log_chat_id_raw)
    except ValueError:
        return
    username = f" (@{html.escape(actor.username)})" if actor.username else ""
    text = (
        f"<b>{title}</b>\n\n"
        f"👤 Кто действие совершил: {ROLE_LABELS[role]} "
        f"{html.escape(actor.full_name)}{username} "
        f"(ID: <code>{actor.id}</code>)\n\n"
        f"⚙️ Действие: {html.escape(action)}"
    )
    if details:
        text += f"\n\n{details}"
    try:
        await bot.send_message(log_chat_id, text)
    except Exception:
        # The database audit remains available even if the log group is absent.
        pass


class AdminCallbackAuditMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and role_of(event.from_user.id):
            if event.data in {
                "ap:home",
                "ap:stats",
                "ap:cards",
                "ap:platforms",
                "ap:payments",
                "ap:team",
                "ap:broadcast",
                "ap:system",
            }:
                state = data.get("state")
                if state is not None:
                    await state.clear()
            await audit_event(
                data["bot"],
                event.from_user,
                "Нажатие inline-кнопки",
                entity_type="callback",
                entity_id=event.data or "",
                details=f"🔘 Callback: <code>{html.escape(event.data or '')}</code>",
            )
        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if (
            user is not None
            and database.get_setting_bool("maintenance_mode")
            and role_of(user.id) is None
        ):
            text = (
                "🛠 В системе ведутся технические работы. "
                "Пожалуйста, зайдите позже."
            )
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        return await handler(event, data)


class StaffFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and role_of(message.from_user.id))


admin_router.callback_query.outer_middleware(AdminCallbackAuditMiddleware())


async def require_callback_role(
    callback: CallbackQuery, allowed: set[str]
) -> str | None:
    role = role_of(callback.from_user.id)
    if role not in allowed:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return None
    return role


async def require_message_role(message: Message, allowed: set[str]) -> str | None:
    if message.from_user is None:
        return None
    role = role_of(message.from_user.id)
    if role not in allowed:
        await message.answer("Недостаточно прав.")
        return None
    return role


async def replace_panel_message(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup,
) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


def home_markup(role: str) -> InlineKeyboardMarkup:
    if role == "owner":
        rows = [
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="ap:stats"),
                InlineKeyboardButton(
                    text=(
                        "🔧 Тех. работы: 🟢 ВКЛ"
                        if database.get_setting_bool("maintenance_mode")
                        else "🔧 Тех. работы: 🔴 ВЫКЛ"
                    ),
                    callback_data="ap:maintenance",
                ),
            ],
            [
                InlineKeyboardButton(text="💳 Карты", callback_data="ap:cards"),
                InlineKeyboardButton(text="🌐 Платформы", callback_data="ap:platforms"),
            ],
            [
                InlineKeyboardButton(text="🏦 Способы оплаты", callback_data="ap:payments"),
                InlineKeyboardButton(text="📢 Рассылка", callback_data="ap:broadcast"),
            ],
            [
                InlineKeyboardButton(text="👥 Команда", callback_data="ap:team"),
                InlineKeyboardButton(text="⚙️ Система", callback_data="ap:system"),
            ],
        ]
    elif role == "supervisor":
        rows = [
            [InlineKeyboardButton(text="💳 Карты", callback_data="ap:cards")],
            [InlineKeyboardButton(text="🌐 Платформы", callback_data="ap:platforms")],
            [InlineKeyboardButton(text="🏦 Способы оплаты", callback_data="ap:payments")],
            [InlineKeyboardButton(text="👥 Менеджеры", callback_data="ap:team")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="💳 Карты", callback_data="ap:cards")],
            [InlineKeyboardButton(text="🌐 Платформы", callback_data="ap:platforms")],
            [InlineKeyboardButton(text="🏦 Способы оплаты", callback_data="ap:payments")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_home(target: Message | CallbackQuery) -> None:
    user = target.from_user
    if user is None:
        return
    role = role_of(user.id)
    if role is None:
        if isinstance(target, CallbackQuery):
            await target.answer("Доступ запрещён.", show_alert=True)
        else:
            await target.answer("Команда доступна только сотрудникам.")
        return
    title = {
        "owner": "👤 <b>PANEL: OWNER</b>",
        "supervisor": "👔 <b>PANEL: УПРАВЛЯЮЩИЙ</b>",
        "manager": "🛠️ <b>PANEL: МЕНЕДЖЕР</b>",
    }[role]
    text = (
        f"{title}\n\n"
        f"Сотрудник: {html.escape(user.full_name)}\n"
        f"ID: <code>{user.id}</code>\n\n"
        "Выберите модуль управления:"
    )
    markup = home_markup(role)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await replace_panel_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@admin_router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_admin_home(message)


@admin_router.message(Command("chatid"))
async def chat_id_command(message: Message) -> None:
    if await require_message_role(message, {"owner"}) is None:
        return
    await message.answer(f"ID этого чата: <code>{message.chat.id}</code>")


@admin_router.message(Command("cancel"), StaffFilter())
async def admin_cancel(message: Message, state: FSMContext) -> None:
    if message.from_user is None or role_of(message.from_user.id) is None:
        return
    await state.clear()
    await show_admin_home(message)


@admin_router.callback_query(F.data == "ap:home")
async def home_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_admin_home(callback)


@admin_router.callback_query(F.data == "ap:stats")
async def statistics_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    report = database.get_admin_statistics()
    cards = database.card_status_counts()
    try:
        deposit_rate = Decimal(
            database.get_setting("profit_deposit_percent", "0")
        )
        withdrawal_rate = Decimal(
            database.get_setting("profit_withdraw_percent", "0")
        )
    except InvalidOperation:
        deposit_rate = withdrawal_rate = Decimal(0)
    period_labels = {
        "today": "Сегодня",
        "week": "Неделя",
        "month": "Месяц",
    }
    lines = ["📊 <b>ФИНАНСОВАЯ СТАТИСТИКА</b>"]
    for key in ("today", "week", "month"):
        data = report[key]
        deposit_profit = int(
            Decimal(data["deposits"]) * deposit_rate / Decimal(100)
        )
        withdrawal_profit = int(
            Decimal(data["withdrawals"]) * withdrawal_rate / Decimal(100)
        )
        lines.extend(
            [
                f"\n<b>{period_labels[key]}</b>",
                f"📥 Ввод: {format_money(data['deposits'])} "
                f"({data['deposit_count']} операций)",
                f"📤 Вывод: {format_money(data['withdrawals'])} "
                f"({data['withdrawal_count']} операций)",
                f"💰 Прибыль: {format_money(deposit_profit + withdrawal_profit)}",
            ]
        )
    lines.extend(
        [
            "\n<b>Техническая нагрузка</b>",
            f"🟢 Активные карты: {cards['active']}",
            f"🟡 Резервные: {cards['reserve']}",
            f"⚫ Лимит исчерпан: {cards['limit_reached']}",
            f"🔴 Отключённые: {cards['disabled']}",
            f"⏳ Ожидающие заявки: {report['technical']['pending']}",
            f"👤 Пользователи: {report['technical']['users']}",
        ]
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏳ Ожидающие заявки", callback_data="ap:pending"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")],
        ]
    )
    await callback.answer()
    await replace_panel_message(callback, "\n".join(lines), markup)


@admin_router.callback_query(F.data == "ap:pending")
async def pending_operations_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    operations = database.list_pending_operations(limit=50)
    await callback.answer()
    if callback.message is None:
        return
    if not operations:
        await callback.message.answer(
            "✅ Ожидающих заявок нет.", reply_markup=back_keyboard("ap:stats")
        )
        return
    await callback.message.answer(
        f"⏳ Ожидающие заявки: <b>{len(operations)}</b>"
    )
    for operation in operations:
        exact = (
            f"\nТочный перевод: <b>{format_money(operation.payment_total_minor)}</b>"
            if operation.payment_total_minor
            else ""
        )
        text = (
            f"<b>Заявка #{operation.id}</b>\n"
            f"Игрок: <code>{operation.user_id}</code>\n"
            f"Тип: {'Пополнение' if operation.kind == 'deposit' else 'Вывод'}\n"
            f"Сумма: <b>{format_money(operation.amount_minor)}</b>"
            f"{exact}"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"operation:approve:{operation.id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"operation:reject:{operation.id}",
                    ),
                ]
            ]
        )
        await callback.message.answer(text, reply_markup=markup)


@admin_router.callback_query(F.data == "ap:maintenance")
async def maintenance_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    enabled = not database.get_setting_bool("maintenance_mode")
    database.set_setting(
        "maintenance_mode", "1" if enabled else "0", callback.from_user.id
    )
    await audit_event(
        bot,
        callback.from_user,
        "Технические работы включены" if enabled else "Технические работы выключены",
        title="🔧 ГЛОБАЛЬНЫЙ СТАТУС СИСТЕМЫ",
        entity_type="system_setting",
        entity_id="maintenance_mode",
        details=f"🚦 Новый статус: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}",
    )
    await show_admin_home(callback)


def platform_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if item['enabled'] else '🔴'} {item['display_name']}",
                callback_data=f"ap:platform:{item['key']}",
            )
        ]
        for item in database.list_platform_settings()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data == "ap:platforms")
async def platforms_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    ) is None:
        return
    await callback.answer()
    await replace_panel_message(
        callback,
        "🌐 <b>РЕДАКТИРОВАТЬ ПЛАТФОРМЫ</b>\n\n"
        "Нажмите платформу, чтобы включить или отключить направление.",
        platform_markup(),
    )


@admin_router.callback_query(F.data.startswith("ap:platform:"))
async def platform_toggle_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    ) is None:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        enabled = database.toggle_platform(key, callback.from_user.id)
    except LookupError:
        await callback.answer("Платформа не найдена.", show_alert=True)
        return
    await audit_event(
        bot,
        callback.from_user,
        f"Платформа {key}: {'включена' if enabled else 'отключена'}",
        title="🌐 ИЗМЕНЕНИЕ ПЛАТФОРМЫ",
        entity_type="platform",
        entity_id=key,
        details=f"🚦 Статус: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}",
    )
    await callback.answer("Статус изменён")
    await replace_panel_message(
        callback,
        "🌐 <b>РЕДАКТИРОВАТЬ ПЛАТФОРМЫ</b>\n\n"
        "Нажмите платформу, чтобы включить или отключить направление.",
        platform_markup(),
    )


def payments_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if item['enabled'] else '🔴'} {item['display_name']}",
                callback_data=f"ap:payment:{item['key']}",
            )
        ]
        for item in database.list_payment_method_settings()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data == "ap:payments")
async def payments_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    ) is None:
        return
    await callback.answer()
    await replace_panel_message(
        callback,
        "🏦 <b>СПОСОБЫ ОПЛАТЫ</b>\n\n"
        "Нажмите банк, чтобы временно включить или отключить его.",
        payments_markup(),
    )


@admin_router.callback_query(F.data.startswith("ap:payment:"))
async def payment_toggle_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    ) is None:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        enabled = database.toggle_payment_method(key, callback.from_user.id)
    except LookupError:
        await callback.answer("Способ оплаты не найден.", show_alert=True)
        return
    await audit_event(
        bot,
        callback.from_user,
        f"Способ оплаты {key}: {'включён' if enabled else 'отключён'}",
        title="🏦 ИЗМЕНЕНИЕ СПОСОБА ОПЛАТЫ",
        entity_type="payment_method",
        entity_id=key,
        details=f"🚦 Статус: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}",
    )
    await callback.answer("Статус изменён")
    await replace_panel_message(
        callback,
        "🏦 <b>СПОСОБЫ ОПЛАТЫ</b>\n\n"
        "Нажмите банк, чтобы временно включить или отключить его.",
        payments_markup(),
    )


def cards_markup(role: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for card in database.list_cards():
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{STATUS_LABELS[card.status].split()[0]} "
                        f"{card.bank_name} •{card.card_number[-4:]}"
                    ),
                    callback_data=f"ap:card:{card.id}",
                )
            ]
        )
    if role in {"owner", "supervisor"}:
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить карту", callback_data="ap:card:add")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data == "ap:cards")
async def cards_callback(callback: CallbackQuery) -> None:
    role = await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    )
    if role is None:
        return
    counts = database.card_status_counts()
    text = (
        "💳 <b>КАРТЫ И АВТОЗАМЕНА</b>\n\n"
        f"🟢 Активные: {counts['active']}\n"
        f"🟡 Резерв: {counts['reserve']}\n"
        f"⚫ Лимит исчерпан: {counts['limit_reached']}\n"
        f"🔴 Отключённые: {counts['disabled']}\n\n"
        "Выберите карту для просмотра и изменения лимитов."
    )
    await callback.answer()
    await replace_panel_message(callback, text, cards_markup(role))


def card_details(card: Card) -> str:
    remaining = (
        max(
            0,
            card.daily_limit_minor
            - card.daily_used_minor
            - card.daily_reserved_minor,
        )
        if card.daily_limit_minor
        else 0
    )
    return (
        f"💳 <b>{html.escape(card.bank_name)}</b>\n\n"
        f"Карта: <code>{mask_card_number(card.card_number)}</code>\n"
        f"Владелец: {html.escape(card.holder_name or 'не указан')}\n"
        f"Направление: {DIRECTION_LABELS[card.direction]}\n"
        f"Статус: {STATUS_LABELS[card.status]}\n\n"
        f"Разовый лимит: "
        f"{format_money(card.single_limit_minor) if card.single_limit_minor else 'без лимита'}\n"
        f"Суточный лимит: "
        f"{format_money(card.daily_limit_minor) if card.daily_limit_minor else 'без лимита'}\n"
        f"Использовано: {format_money(card.daily_used_minor)}\n"
        f"Зарезервировано: {format_money(card.daily_reserved_minor)}\n"
        f"Остаток: "
        f"{format_money(remaining) if card.daily_limit_minor else 'без лимита'}"
    )


def card_details_markup(card: Card, role: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📉 Суточный лимит",
                callback_data=f"ap:cardlimit:{card.id}:daily",
            ),
            InlineKeyboardButton(
                text="🔢 Разовый лимит",
                callback_data=f"ap:cardlimit:{card.id}:single",
            ),
        ]
    ]
    if role in {"owner", "supervisor"}:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🟢 Активна",
                        callback_data=f"ap:cardstatus:{card.id}:active",
                    ),
                    InlineKeyboardButton(
                        text="🟡 Резерв",
                        callback_data=f"ap:cardstatus:{card.id}:reserve",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔴 Отключить",
                        callback_data=f"ap:cardstatus:{card.id}:disabled",
                    ),
                    InlineKeyboardButton(
                        text="❌ Удалить", callback_data=f"ap:carddelete:{card.id}"
                    ),
                ],
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="ap:cards")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data.regexp(r"^ap:card:\d+$"))
async def card_view_callback(callback: CallbackQuery) -> None:
    role = await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    )
    if role is None:
        return
    card_id = int((callback.data or "").rsplit(":", 1)[-1])
    card = database.get_card(card_id)
    if card is None:
        await callback.answer("Карта не найдена.", show_alert=True)
        return
    await callback.answer()
    await replace_panel_message(
        callback, card_details(card), card_details_markup(card, role)
    )


@admin_router.callback_query(F.data == "ap:card:add")
async def card_add_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    rows = [
        [
            InlineKeyboardButton(
                text=str(item["display_name"]),
                callback_data=f"ap:cardaddbank:{item['key']}",
            )
        ]
        for item in database.list_payment_method_settings()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:cards")])
    await state.clear()
    await callback.answer()
    await replace_panel_message(
        callback,
        "➕ <b>ДОБАВЛЕНИЕ КАРТЫ</b>\n\nВыберите банк:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_router.callback_query(F.data.startswith("ap:cardaddbank:"))
async def card_add_bank_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    key = (callback.data or "").split(":", 2)[-1]
    method = next(
        (
            item
            for item in database.list_payment_method_settings()
            if str(item["key"]) == key
        ),
        None,
    )
    if method is None:
        await callback.answer("Банк не найден.", show_alert=True)
        return
    await state.update_data(
        admin_bank_key=key, admin_bank_name=str(method["display_name"])
    )
    await state.set_state(AdminStates.card_number)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите номер карты, телефона или счёта.\n"
            "Для отмены используйте /cancel."
        )


@admin_router.message(AdminStates.card_number)
async def card_number_message(message: Message, state: FSMContext) -> None:
    if await require_message_role(message, {"owner", "supervisor"}) is None:
        return
    value = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9+() _-]{4,64}", value):
        await message.answer("Введите корректные реквизиты длиной от 4 до 64 символов.")
        return
    await state.update_data(admin_card_number=value)
    await state.set_state(AdminStates.card_holder)
    await message.answer("Введите имя владельца карты или отправьте знак <code>-</code>.")


@admin_router.message(AdminStates.card_holder)
async def card_holder_message(message: Message, state: FSMContext) -> None:
    if await require_message_role(message, {"owner", "supervisor"}) is None:
        return
    holder = (message.text or "").strip()
    if not holder or len(holder) > 100:
        await message.answer("Введите имя длиной до 100 символов.")
        return
    await state.update_data(admin_card_holder="" if holder == "-" else holder)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Ввод", callback_data="ap:carddirection:deposit"
                ),
                InlineKeyboardButton(
                    text="📤 Вывод", callback_data="ap:carddirection:withdrawal"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↔️ Ввод и вывод", callback_data="ap:carddirection:both"
                )
            ],
        ]
    )
    await message.answer("Выберите назначение карты:", reply_markup=markup)


@admin_router.callback_query(F.data.startswith("ap:carddirection:"))
async def card_direction_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    direction = (callback.data or "").rsplit(":", 1)[-1]
    if direction not in DIRECTION_LABELS:
        await callback.answer("Неверное направление.", show_alert=True)
        return
    await state.update_data(admin_card_direction=direction)
    await state.set_state(AdminStates.card_daily_limit)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите суточный лимит в KGS. Отправьте <code>0</code>, если лимита нет."
        )


@admin_router.message(AdminStates.card_daily_limit)
async def card_daily_limit_message(message: Message, state: FSMContext) -> None:
    if await require_message_role(message, {"owner", "supervisor"}) is None:
        return
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer("Введите число, например <code>100000</code>.")
        return
    await state.update_data(admin_card_daily=value)
    await state.set_state(AdminStates.card_single_limit)
    await message.answer(
        "Введите максимальный разовый платёж в KGS. "
        "Отправьте <code>0</code>, если лимита нет."
    )


@admin_router.message(AdminStates.card_single_limit)
async def card_single_limit_message(message: Message, state: FSMContext) -> None:
    if await require_message_role(message, {"owner", "supervisor"}) is None:
        return
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer("Введите число, например <code>10000</code>.")
        return
    await state.update_data(admin_card_single=value)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Активная", callback_data="ap:cardcreate:active"
                ),
                InlineKeyboardButton(
                    text="🟡 В резерв", callback_data="ap:cardcreate:reserve"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔴 Отключённая", callback_data="ap:cardcreate:disabled"
                )
            ],
        ]
    )
    await message.answer("Выберите начальный статус карты:", reply_markup=markup)


@admin_router.callback_query(F.data.startswith("ap:cardcreate:"))
async def card_create_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    status = (callback.data or "").rsplit(":", 1)[-1]
    data = await state.get_data()
    required = {
        "admin_bank_key",
        "admin_bank_name",
        "admin_card_number",
        "admin_card_holder",
        "admin_card_direction",
        "admin_card_daily",
        "admin_card_single",
    }
    if not required.issubset(data):
        await callback.answer("Сессия добавления истекла.", show_alert=True)
        return
    card = database.add_card(
        str(data["admin_bank_key"]),
        str(data["admin_bank_name"]),
        str(data["admin_card_number"]),
        str(data["admin_card_holder"]),
        str(data["admin_card_direction"]),
        status,
        int(data["admin_card_single"]),
        int(data["admin_card_daily"]),
        callback.from_user.id,
    )
    await state.clear()
    await audit_event(
        bot,
        callback.from_user,
        "Добавлена новая карта",
        title="💳 ИЗМЕНЕНИЕ КАРТОЧНОГО ПУЛА",
        entity_type="card",
        entity_id=str(card.id),
        details=(
            f"🏦 Банк: {html.escape(card.bank_name)}\n"
            f"💳 Карта: <code>{mask_card_number(card.card_number)}</code>\n"
            f"🚦 Статус: {STATUS_LABELS[card.status]}"
        ),
    )
    await callback.answer("Карта добавлена")
    if callback.message:
        await callback.message.answer(
            "✅ Карта успешно добавлена.",
            reply_markup=back_keyboard("ap:cards"),
        )


@admin_router.callback_query(F.data.startswith("ap:cardlimit:"))
async def card_limit_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(
        callback, {"owner", "supervisor", "manager"}
    ) is None:
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[3] not in {"daily", "single"}:
        await callback.answer("Неверная команда.", show_alert=True)
        return
    await state.update_data(admin_card_id=int(parts[2]), admin_limit_type=parts[3])
    await state.set_state(AdminStates.card_limit_value)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите новый лимит в KGS. Значение <code>0</code> отключает лимит."
        )


@admin_router.message(AdminStates.card_limit_value)
async def card_limit_value_message(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if await require_message_role(
        message, {"owner", "supervisor", "manager"}
    ) is None:
        return
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("Введите корректное число.")
        return
    data = await state.get_data()
    try:
        old, card = database.update_card_limit(
            int(data["admin_card_id"]), str(data["admin_limit_type"]), amount
        )
    except (KeyError, LookupError, ValueError):
        await state.clear()
        await message.answer("Карта не найдена или сессия истекла.")
        return
    await state.clear()
    limit_name = (
        "суточного лимита"
        if data["admin_limit_type"] == "daily"
        else "разового лимита"
    )
    await audit_event(
        bot,
        message.from_user,
        f"Изменение {limit_name}",
        title="💳 ИЗМЕНЕНИЕ ЛИМИТОВ КАРТЫ",
        entity_type="card",
        entity_id=str(card.id),
        details=(
            f"💳 Карта: {html.escape(card.bank_name)} "
            f"(<code>{mask_card_number(card.card_number)}</code>)\n"
            f"📉 Старый лимит: {format_money(old)}\n"
            f"📈 Новый лимит: {format_money(amount)}"
        ),
    )
    await message.answer(
        f"✅ Новый лимит сохранён: <b>{format_money(amount)}</b>",
        reply_markup=back_keyboard(f"ap:card:{card.id}"),
    )


@admin_router.callback_query(F.data.startswith("ap:cardstatus:"))
async def card_status_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Неверная команда.", show_alert=True)
        return
    try:
        old, card = database.set_card_status(int(parts[2]), parts[3])
    except (LookupError, ValueError):
        await callback.answer("Карта не найдена.", show_alert=True)
        return
    await audit_event(
        bot,
        callback.from_user,
        "Изменён статус карты",
        title="💳 ИЗМЕНЕНИЕ КАРТОЧНОГО ПУЛА",
        entity_type="card",
        entity_id=str(card.id),
        details=(
            f"💳 Карта: {html.escape(card.bank_name)} "
            f"(<code>{mask_card_number(card.card_number)}</code>)\n"
            f"Старый статус: {STATUS_LABELS.get(old, old)}\n"
            f"Новый статус: {STATUS_LABELS[card.status]}"
        ),
    )
    await callback.answer("Статус изменён")
    await replace_panel_message(
        callback,
        card_details(card),
        card_details_markup(card, role_of(callback.from_user.id) or "manager"),
    )


@admin_router.callback_query(F.data.startswith("ap:carddelete:"))
async def card_delete_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    card_id = int((callback.data or "").rsplit(":", 1)[-1])
    card = database.get_card(card_id)
    if card is None:
        await callback.answer("Карта не найдена.", show_alert=True)
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Да, удалить", callback_data=f"ap:carddeleteyes:{card_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена", callback_data=f"ap:card:{card_id}"
                )
            ],
        ]
    )
    await callback.answer()
    await replace_panel_message(
        callback,
        f"Удалить карту {html.escape(card.bank_name)} "
        f"<code>{mask_card_number(card.card_number)}</code>?",
        markup,
    )


@admin_router.callback_query(F.data.startswith("ap:carddeleteyes:"))
async def card_delete_confirm_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await require_callback_role(callback, {"owner", "supervisor"}) is None:
        return
    card_id = int((callback.data or "").rsplit(":", 1)[-1])
    card = database.delete_card(card_id)
    if card is None:
        await callback.answer("Карта уже удалена.", show_alert=True)
        return
    await audit_event(
        bot,
        callback.from_user,
        "Удалена карта",
        title="💳 ИЗМЕНЕНИЕ КАРТОЧНОГО ПУЛА",
        entity_type="card",
        entity_id=str(card.id),
        details=(
            f"🏦 Банк: {html.escape(card.bank_name)}\n"
            f"💳 Карта: <code>{mask_card_number(card.card_number)}</code>"
        ),
    )
    await callback.answer("Карта удалена")
    role = role_of(callback.from_user.id) or "manager"
    await replace_panel_message(
        callback, "✅ Карта удалена.", cards_markup(role)
    )


def team_markup(role: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if role == "owner":
        rows.append(
            [
                InlineKeyboardButton(
                    text="👔 Управляющие", callback_data="ap:teamlist:supervisor"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🛠 Менеджеры", callback_data="ap:teamlist:manager")]
    )
    if role == "owner":
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Управляющий", callback_data="ap:teamadd:supervisor"
                ),
                InlineKeyboardButton(
                    text="➕ Менеджер", callback_data="ap:teamadd:manager"
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить менеджера", callback_data="ap:teamadd:manager"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data == "ap:team")
async def team_callback(callback: CallbackQuery) -> None:
    role = await require_callback_role(callback, {"owner", "supervisor"})
    if role is None:
        return
    await callback.answer()
    await replace_panel_message(
        callback,
        "👥 <b>КОМАНДА</b>\n\n"
        "Просмотр, назначение и мгновенное прекращение доступа сотрудников.",
        team_markup(role),
    )


@admin_router.callback_query(F.data.startswith("ap:teamlist:"))
async def team_list_callback(callback: CallbackQuery) -> None:
    actor_role = await require_callback_role(callback, {"owner", "supervisor"})
    if actor_role is None:
        return
    target_role = (callback.data or "").rsplit(":", 1)[-1]
    if target_role == "supervisor" and actor_role != "owner":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    members = database.list_staff(target_role)
    rows: list[list[InlineKeyboardButton]] = []
    for member in members:
        username = f"@{member['username']}" if member["username"] else str(member["user_id"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {username}",
                    callback_data=f"ap:teamfire:{member['user_id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:team")])
    title = "Управляющие" if target_role == "supervisor" else "Менеджеры"
    text = f"👥 <b>{title}</b>\n\n" + (
        f"Всего: {len(members)}" if members else "Список пуст."
    )
    await callback.answer()
    await replace_panel_message(
        callback, text, InlineKeyboardMarkup(inline_keyboard=rows)
    )


@admin_router.callback_query(F.data.startswith("ap:teamadd:"))
async def team_add_callback(callback: CallbackQuery, state: FSMContext) -> None:
    actor_role = await require_callback_role(callback, {"owner", "supervisor"})
    if actor_role is None:
        return
    target_role = (callback.data or "").rsplit(":", 1)[-1]
    if target_role == "supervisor" and actor_role != "owner":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if target_role not in {"supervisor", "manager"}:
        await callback.answer("Неверная роль.", show_alert=True)
        return
    await state.update_data(admin_target_role=target_role)
    await state.set_state(AdminStates.team_identifier)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите Telegram ID или @username сотрудника.\n\n"
            "Пользователь должен предварительно открыть бота и нажать /start."
        )


@admin_router.message(AdminStates.team_identifier)
async def team_identifier_message(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    actor_role = await require_message_role(message, {"owner", "supervisor"})
    if actor_role is None:
        return
    data = await state.get_data()
    target_role = str(data.get("admin_target_role", ""))
    if target_role == "supervisor" and actor_role != "owner":
        await state.clear()
        await message.answer("Недостаточно прав.")
        return
    user = database.resolve_user(message.text or "")
    if user is None:
        await message.answer(
            "Пользователь не найден. Попросите его нажать /start и повторите."
        )
        return
    target_id = int(user["user_id"])
    existing_role = role_of(target_id)
    if existing_role == "owner":
        await message.answer("Нельзя изменить роль Владельца.")
        return
    if actor_role == "supervisor" and existing_role == "supervisor":
        await message.answer("Управляющий не может изменять других Управляющих.")
        return
    member = database.set_staff_role(
        target_id, target_role, message.from_user.id
    )
    await state.clear()
    role_label = ROLE_LABELS[target_role]
    username = f"@{member['username']}" if member["username"] else "без username"
    await audit_event(
        bot,
        message.from_user,
        f"Назначен новый {role_label}",
        title="🛡️ ИЗМЕНЕНИЕ В КОМАНДЕ ПРОЕКТА",
        entity_type="staff",
        entity_id=str(target_id),
        details=(
            f"👔 Кому выданы права: {html.escape(username)} "
            f"(ID: <code>{target_id}</code>)\n"
            f"ℹ️ Новая роль: {role_label}"
        ),
    )
    try:
        await bot.send_message(
            target_id,
            f"✅ Вам предоставлен доступ к админ-панели.\n"
            f"Роль: <b>{role_label}</b>\nКоманда: /admin",
        )
    except Exception:
        pass
    await message.answer(
        f"✅ {role_label} добавлен: {html.escape(username)} "
        f"(<code>{target_id}</code>)",
        reply_markup=back_keyboard("ap:team"),
    )


@admin_router.callback_query(F.data.startswith("ap:teamfire:"))
async def team_fire_callback(callback: CallbackQuery) -> None:
    actor_role = await require_callback_role(callback, {"owner", "supervisor"})
    if actor_role is None:
        return
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    target_role = role_of(target_id)
    if target_role is None or target_role == "owner":
        await callback.answer("Сотрудник не найден или защищён.", show_alert=True)
        return
    if actor_role == "supervisor" and target_role != "manager":
        await callback.answer("Управляющий может увольнять только менеджеров.", show_alert=True)
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Лишить доступа", callback_data=f"ap:teamfireyes:{target_id}"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ap:team")],
        ]
    )
    await callback.answer()
    await replace_panel_message(
        callback,
        f"Лишить сотрудника <code>{target_id}</code> доступа к панели?",
        markup,
    )


@admin_router.callback_query(F.data.startswith("ap:teamfireyes:"))
async def team_fire_confirm_callback(callback: CallbackQuery, bot: Bot) -> None:
    actor_role = await require_callback_role(callback, {"owner", "supervisor"})
    if actor_role is None:
        return
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    target_role = role_of(target_id)
    if target_role is None or target_role == "owner":
        await callback.answer("Сотрудник не найден или защищён.", show_alert=True)
        return
    if actor_role == "supervisor" and target_role != "manager":
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not database.remove_staff(target_id):
        await callback.answer("Доступ уже прекращён.", show_alert=True)
        return
    await audit_event(
        bot,
        callback.from_user,
        "Сотрудник уволен и лишён прав",
        title="🛡️ ИЗМЕНЕНИЕ В КОМАНДЕ ПРОЕКТА",
        entity_type="staff",
        entity_id=str(target_id),
        details=(
            f"👤 Сотрудник: <code>{target_id}</code>\n"
            f"ℹ️ Прежняя роль: {ROLE_LABELS[target_role]}"
        ),
    )
    try:
        await bot.send_message(target_id, "❌ Ваш доступ к админ-панели прекращён.")
    except Exception:
        pass
    await callback.answer("Доступ прекращён")
    await replace_panel_message(
        callback, "✅ Сотрудник лишён доступа.", back_keyboard("ap:team")
    )


@admin_router.callback_query(F.data == "ap:broadcast")
async def broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    await state.set_state(AdminStates.broadcast_content)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📢 Отправьте сообщение для рассылки. Поддерживается текст, фото, "
            "видео или документ. После этого бот покажет предпросмотр.\n\n"
            "Отмена: /cancel"
        )


@admin_router.message(AdminStates.broadcast_content)
async def broadcast_content_message(message: Message, state: FSMContext) -> None:
    if await require_message_role(message, {"owner"}) is None:
        return
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await message.answer("👁 <b>ПРЕДПРОСМОТР РАССЫЛКИ</b>")
    try:
        await message.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        await message.answer("Не удалось создать предпросмотр этого типа сообщения.")
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Начать рассылку", callback_data="ap:broadcast:confirm"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ap:home")],
        ]
    )
    await message.answer("Отправить это сообщение всем пользователям?", reply_markup=markup)


async def run_broadcast(
    bot: Bot,
    actor: User,
    source_chat_id: int,
    source_message_id: int,
) -> None:
    delivered = 0
    blocked = 0
    failed = 0
    for user_id in database.list_user_ids():
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            delivered += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.045)
    report = (
        "📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
        f"✅ Успешно доставлено: {delivered}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Другие ошибки: {failed}"
    )
    try:
        await bot.send_message(actor.id, report)
    except Exception:
        pass
    await audit_event(
        bot,
        actor,
        "Массовая рассылка завершена",
        title="📢 МАССОВАЯ РАССЫЛКА",
        entity_type="broadcast",
        details=(
            f"✅ Доставлено: {delivered}\n"
            f"🚫 Заблокировали: {blocked}\n"
            f"⚠️ Ошибки: {failed}"
        ),
    )


@admin_router.callback_query(F.data == "ap:broadcast:confirm")
async def broadcast_confirm_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    data = await state.get_data()
    if "broadcast_chat_id" not in data or "broadcast_message_id" not in data:
        await callback.answer("Предпросмотр не найден.", show_alert=True)
        return
    await state.clear()
    await audit_event(
        bot,
        callback.from_user,
        "Запущена массовая рассылка",
        title="📢 МАССОВАЯ РАССЫЛКА",
        entity_type="broadcast",
    )
    asyncio.create_task(
        run_broadcast(
            bot,
            callback.from_user,
            int(data["broadcast_chat_id"]),
            int(data["broadcast_message_id"]),
        )
    )
    await callback.answer("Рассылка запущена")
    if callback.message:
        await callback.message.answer(
            "⏳ Рассылка выполняется в фоне. По окончании придёт отчёт."
        )


def system_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗂 ID чата логов", callback_data="ap:systemset:log_chat_id"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Контакт поддержки",
                    callback_data="ap:systemset:support_contact",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Текст оферты", callback_data="ap:systemset:offer_text"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 % прибыли со ввода",
                    callback_data="ap:systemset:profit_deposit_percent",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 % прибыли с вывода",
                    callback_data="ap:systemset:profit_withdraw_percent",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap:home")],
        ]
    )


@admin_router.callback_query(F.data == "ap:system")
async def system_callback(callback: CallbackQuery) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    log_chat = database.get_setting("log_chat_id", "не задан") or "не задан"
    support = database.get_setting("support_contact", "") or settings.support_username or "не задан"
    offer = database.get_setting("offer_text", "")
    text = (
        "⚙️ <b>РЕДАКТИРОВАНИЕ СИСТЕМЫ</b>\n\n"
        f"🗂 Чат логов: <code>{html.escape(log_chat)}</code>\n"
        f"🆘 Поддержка: {html.escape(support)}\n"
        f"📄 Оферта: {'свой текст' if offer else 'стандартный текст'}\n"
        f"📥 Прибыль со ввода: "
        f"{html.escape(database.get_setting('profit_deposit_percent', '0'))}%\n"
        f"📤 Прибыль с вывода: "
        f"{html.escape(database.get_setting('profit_withdraw_percent', '0'))}%"
    )
    await callback.answer()
    await replace_panel_message(callback, text, system_markup())


@admin_router.callback_query(F.data.startswith("ap:systemset:"))
async def system_set_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await require_callback_role(callback, {"owner"}) is None:
        return
    key = (callback.data or "").rsplit(":", 1)[-1]
    prompts = {
        "log_chat_id": (
            "Введите ID закрытой группы логов, например <code>-1001234567890</code>. "
            "Добавьте бота в группу. ID можно узнать командой /chatid внутри группы."
        ),
        "support_contact": (
            "Введите @username или полную HTTPS-ссылку поддержки."
        ),
        "offer_text": (
            "Отправьте новый полный текст оферты. Допускается до 3500 символов."
        ),
        "profit_deposit_percent": "Введите процент прибыли со ввода от 0 до 100.",
        "profit_withdraw_percent": "Введите процент прибыли с вывода от 0 до 100.",
    }
    if key not in prompts:
        await callback.answer("Неизвестная настройка.", show_alert=True)
        return
    await state.update_data(admin_system_key=key)
    await state.set_state(AdminStates.system_value)
    await callback.answer()
    if callback.message:
        await callback.message.answer(prompts[key] + "\n\nОтмена: /cancel")


@admin_router.message(AdminStates.system_value)
async def system_value_message(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if await require_message_role(message, {"owner"}) is None:
        return
    data = await state.get_data()
    key = str(data.get("admin_system_key", ""))
    value = (message.text or "").strip()
    if key == "log_chat_id":
        if not re.fullmatch(r"-?\d{5,20}", value):
            await message.answer("Введите корректный числовой ID группы.")
            return
    elif key == "support_contact":
        if not (
            re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value)
            or re.fullmatch(r"https://[^\s]{5,500}", value)
        ):
            await message.answer("Введите @username или HTTPS-ссылку.")
            return
    elif key == "offer_text":
        if not 20 <= len(value) <= 3500:
            await message.answer("Текст оферты должен содержать от 20 до 3500 символов.")
            return
    elif key in {"profit_deposit_percent", "profit_withdraw_percent"}:
        try:
            percent = Decimal(value.replace(",", "."))
        except InvalidOperation:
            await message.answer("Введите число от 0 до 100.")
            return
        if percent < 0 or percent > 100:
            await message.answer("Процент должен быть от 0 до 100.")
            return
        value = format(percent.normalize(), "f")
    else:
        await state.clear()
        await message.answer("Неизвестная настройка.")
        return
    old = database.set_setting(key, value, message.from_user.id)
    await state.clear()
    label = {
        "log_chat_id": "ID чата логов",
        "support_contact": "контакт поддержки",
        "offer_text": "текст оферты",
        "profit_deposit_percent": "процент прибыли со ввода",
        "profit_withdraw_percent": "процент прибыли с вывода",
    }[key]
    details = f"⚙️ Настройка: {html.escape(label)}"
    if key != "offer_text":
        details += (
            f"\n📉 Старое значение: <code>{html.escape(old or 'не задано')}</code>"
            f"\n📈 Новое значение: <code>{html.escape(value)}</code>"
        )
    await audit_event(
        bot,
        message.from_user,
        f"Изменён {label}",
        title="⚙️ ИЗМЕНЕНИЕ СИСТЕМЫ",
        entity_type="system_setting",
        entity_id=key,
        details=details,
    )
    await message.answer(
        f"✅ Сохранено: {html.escape(label)}.",
        reply_markup=back_keyboard("ap:system"),
    )
