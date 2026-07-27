from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from io import BytesIO
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

import qrcode
from aiogram import Bot, Dispatcher, F, Router
from aiohttp import web
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

from config import PaymentMethod, Settings, load_settings
from database import Database, Operation, UserProfile
from admin_panel import (
    MaintenanceMiddleware,
    admin_router,
    audit_event,
    configure_admin_panel,
    show_admin_home,
)


router = Router()
settings: Settings
database: Database
bot_username = ""
payment_expiry_tasks: dict[int, asyncio.Task[None]] = {}


TERMS_RU = """
<b>⚖️ ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ
И РЕГЛАМЕНТ ПРОВЕДЕНИЯ ПЛАТЕЖЕЙ</b>

1. Сервис принимает заявки на пополнение и вывод средств для указанных пользователем игровых платформ.

2. Пользователь обязан проверить платформу, игровой ID, сумму и платёжные реквизиты до отправки заявки. Ошибочные данные могут привести к задержке или отклонению операции.

3. Пополнение обрабатывается после получения оплаты и подтверждающего чека. Сумма чека должна совпадать с суммой заявки.

4. Для вывода пользователь отправляет реквизиты получения и подтверждение заявки, созданной в личном кабинете игровой платформы.

5. Сервис никогда не запрашивает пароль, PIN-код или CVV банковской карты. Не передавайте эти данные никому.

6. Запрещены чужие реквизиты, поддельные чеки, повторное использование одного чека и любые мошеннические действия. Подозрительная заявка может быть отклонена и передана на дополнительную проверку.

7. Срок обработки зависит от платёжной системы и проверки администратором. Статус каждой заявки доступен в истории операций.

Нажимая «Принимаю», вы подтверждаете ознакомление с правилами и согласие на обработку данных, необходимых для выполнения заявки.
""".strip()


TERMS_EN = """
<b>⚖️ USER AGREEMENT
AND PAYMENT PROCESSING RULES</b>

1. The service accepts deposit and withdrawal requests for the gaming platforms selected by the user.

2. The user must verify the platform, gaming account ID, amount and payment details before submitting a request. Incorrect information may delay or cancel the operation.

3. A deposit is processed after payment and receipt verification. The receipt amount must match the request amount.

4. For a withdrawal, the user submits payout details and proof of the request created in the gaming platform account.

5. The service never asks for a password, card PIN or CVV. Never share these details.

6. Third-party details, forged receipts, reuse of a receipt and fraudulent activity are prohibited. A suspicious request may be rejected or reviewed manually.

7. Processing time depends on the payment provider and administrator verification. Request status is available in operation history.

By pressing “Accept”, you confirm that you have read these rules and consent to processing the information required to complete your request.
""".strip()


TERMS_KY = """
<b>⚖️ КОЛДОНУУЧУ КЕЛИШИМИ
ЖАНА ТӨЛӨМДӨРДҮ ЖҮРГҮЗҮҮ ЭРЕЖЕЛЕРИ</b>

1. Сервис колдонуучу тандаган оюн платформалары үчүн эсепти толуктоо жана каражат чыгаруу өтүнмөлөрүн кабыл алат.

2. Колдонуучу өтүнмө жөнөтүүдөн мурун платформаны, оюн ID-син, сумманы жана төлөм реквизиттерин текшерүүгө милдеттүү. Туура эмес маалымат операциянын кечигишине же четке кагылышына алып келиши мүмкүн.

3. Эсепти толуктоо төлөм алынгандан жана чек текшерилгенден кийин аткарылат. Чектеги сумма өтүнмөдөгү суммага дал келиши керек.

4. Каражат чыгаруу үчүн колдонуучу алуу реквизиттерин жана оюн платформасында түзүлгөн өтүнмөнүн ырастоосун жөнөтөт.

5. Сервис эч качан сырсөздү, картанын PIN-кодун же CVV кодун сурабайт. Бул маалыматтарды эч кимге бербеңиз.

6. Башка адамдын реквизиттерин, жасалма чектерди, бир чекти кайра колдонууну жана алдамчылык аракеттерди колдонууга тыюу салынат. Шектүү өтүнмө четке кагылышы же кошумча текшерүүгө жөнөтүлүшү мүмкүн.

7. Иштетүү мөөнөтү төлөм системасына жана администратордун текшерүүсүнө жараша болот. Өтүнмөнүн статусу операциялар тарыхында көрсөтүлөт.

«Кабыл алам» баскычын басуу менен сиз эрежелерди окуганыңызды жана өтүнмөнү аткаруу үчүн керектүү маалыматтарды иштетүүгө макул экениңизди ырастайсыз.
""".strip()


SUPPORTED_LANGUAGES = {"ru", "en", "ky"}


MAIN_LABELS = {
    "deposit": {"ru": "💳 Пополнить", "en": "💳 Deposit", "ky": "💳 Толуктоо"},
    "withdrawal": {"ru": "💸 Вывести", "en": "💸 Withdraw", "ky": "💸 Чыгаруу"},
    "profile": {"ru": "Профиль", "en": "Profile", "ky": "Профиль"},
    "terms": {
        "ru": "Соглашение и правила",
        "en": "Terms and rules",
        "ky": "Келишим жана эрежелер",
    },
    "support": {"ru": "👨‍💼 Поддержка", "en": "👨‍💼 Support", "ky": "👨‍💼 Колдоо"},
    "language": {"ru": "🌐 Язык", "en": "🌐 Language", "ky": "🌐 Тил"},
}

YELLOW_MAIN_LABELS = {
    "deposit": {"ru": "🟨 Пополнить", "en": "🟨 Deposit", "ky": "🟨 Толуктоо"},
    "withdrawal": {"ru": "🟨 Вывести", "en": "🟨 Withdraw", "ky": "🟨 Чыгаруу"},
    "profile": {"ru": "🟨 Профиль", "en": "🟨 Profile", "ky": "🟨 Профиль"},
    "terms": {
        "ru": "🟨 Соглашение и правила",
        "en": "🟨 Terms and rules",
        "ky": "🟨 Келишим жана эрежелер",
    },
    "support": {"ru": "🟨 Тех поддержка", "en": "🟨 Support", "ky": "🟨 Колдоо"},
    "language": {"ru": "🟨 Язык", "en": "🟨 Language", "ky": "🟨 Тил"},
}

LEGACY_MAIN_LABELS = {
    "deposit": {"ru": "💰 Пополнить", "en": "💰 Deposit", "ky": "💰 Толуктоо"},
    "withdrawal": {"ru": "💸 Вывести", "en": "💸 Withdraw", "ky": "💸 Чыгаруу"},
    "profile": {"ru": "👤 Профиль", "en": "👤 Profile", "ky": "👤 Профиль"},
    "terms": {
        "ru": "📜 Соглашение и правила",
        "en": "📜 Terms and rules",
        "ky": "📜 Келишим жана эрежелер",
    },
    "support": {"ru": "👨‍💼 Тех поддержка", "en": "👨‍💼 Support", "ky": "👨‍💼 Колдоо"},
    "language": {"ru": "🌐 Язык", "en": "🌐 Language", "ky": "🌐 Тил"},
}


def uniform_button_text(text: str) -> str:
    return text.strip()


def strip_uniform_button_icon(text: str) -> str:
    normalized = text.strip()
    if normalized.startswith("🟨"):
        return normalized[len("🟨") :].strip()
    return normalized


def platform_button_text(platform: str) -> str:
    return platform


def platform_button_icon(platform: str) -> str | None:
    return settings.platform_icon_custom_emoji_ids.get(platform)


def platform_from_button_text(text: str) -> str | None:
    selected = text.strip().casefold()
    for platform in settings.platforms:
        if selected in {
            platform.casefold(),
            platform_button_text(platform).casefold(),
            f"🟨 {platform}".casefold(),
        }:
            return platform
    return None


class DepositFlow(StatesGroup):
    platform = State()
    account_id = State()
    amount = State()
    payment = State()
    proof = State()


class WithdrawalFlow(StatesGroup):
    platform = State()
    account_id = State()
    amount = State()
    payout = State()
    evidence = State()


class ReferralFlow(StatesGroup):
    platform = State()
    account_id = State()


def register_user(user: User) -> None:
    database.upsert_user(user.id, user.username, user.full_name)


def get_profile(user_id: int) -> UserProfile | None:
    return database.get_user(user_id)


def get_language(user_id: int) -> str:
    profile = get_profile(user_id)
    return profile.language if profile and profile.language in SUPPORTED_LANGUAGES else "ru"


def translate(language: str, ru: str, en: str, ky: str | None = None) -> str:
    if language == "en":
        return en
    if language == "ky":
        return ky if ky is not None else ru
    return ru


def localize(user_id: int, ru: str, en: str, ky: str | None = None) -> str:
    return translate(get_language(user_id), ru, en, ky)


def main_keyboard(language: str) -> ReplyKeyboardMarkup:
    lang = language if language in SUPPORTED_LANGUAGES else "ru"
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=MAIN_LABELS["deposit"][lang], style="success"
                ),
                KeyboardButton(
                    text=MAIN_LABELS["withdrawal"][lang], style="success"
                ),
            ],
            [
                KeyboardButton(
                    text=MAIN_LABELS["support"][lang], style="success"
                ),
            ],
            [
                KeyboardButton(
                    text=MAIN_LABELS["language"][lang], style="success"
                ),
            ],
        ],
        resize_keyboard=True,
    )


def platforms_reply_keyboard(language: str) -> ReplyKeyboardMarkup:
    buttons = [
        KeyboardButton(
            text=platform_button_text(platform),
            icon_custom_emoji_id=platform_button_icon(platform),
            style="success",
        )
        for platform in settings.platforms
        if database.is_platform_enabled(platform)
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            KeyboardButton(
                text=uniform_button_text(
                    translate(language, "Отмена", "Cancel", "Жокко чыгаруу")
                ),
                style="danger",
            )
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def amount_reply_keyboard(flow: str, language: str) -> ReplyKeyboardMarkup:
    values = (
        (100, 200, 500, 1000, 2000, 5000, 10000)
        if flow == "deposit"
        else (500, 1000, 2000, 5000, 10000)
    )
    rows: list[list[KeyboardButton]] = []
    for index in range(0, 6, 3):
        rows.append(
            [
                KeyboardButton(
                    text=uniform_button_text(str(value)),
                    style="success",
                )
                for value in values[index : index + 3]
            ]
        )
    if len(values) > 6:
        rows.append(
            [
                KeyboardButton(
                    text=uniform_button_text(str(values[6])),
                    style="success",
                )
            ]
        )
    rows.append(
        [
            KeyboardButton(
                text=uniform_button_text(
                    translate(language, "Отмена", "Cancel", "Жокко чыгаруу")
                ),
                style="danger",
            )
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uniform_button_text("Русский"),
                    callback_data="language:ru",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=uniform_button_text("English"),
                    callback_data="language:en",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=uniform_button_text("🇰🇬 Кыргызча"),
                    callback_data="language:ky",
                    style="success",
                )
            ],
        ]
    )


def subscription_keyboard() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    channel_url = settings.required_channel_url
    if not channel_url and settings.required_channel and settings.required_channel.startswith("@"):
        channel_url = f"https://t.me/{settings.required_channel[1:]}"
    if channel_url:
        buttons.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)])
    buttons.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="subscription:check")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def has_required_subscription(bot: Bot, telegram_id: int) -> bool:
    if not settings.required_channel or telegram_id in settings.admin_ids:
        return True
    try:
        member = await bot.get_chat_member(settings.required_channel, telegram_id)
    except Exception:
        logging.exception("Unable to check required channel subscription")
        return False
    if member.status in {"creator", "owner", "administrator", "member"}:
        return True
    return bool(getattr(member, "is_member", False))


async def require_subscription(message: Message, user: User, bot: Bot) -> bool:
    if await has_required_subscription(bot, user.id):
        return True
    await message.answer(
        "📢 Для использования бота подпишитесь на наш канал, затем нажмите «Проверить подписку».",
        reply_markup=subscription_keyboard(),
    )
    return False


def agreement_keyboard(language: str) -> InlineKeyboardMarkup:
    accept = translate(language, "Принимаю", "Accept", "Кабыл алам")
    decline = translate(language, "Не принимаю", "Decline", "Кабыл албайм")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uniform_button_text(accept),
                    callback_data="terms:accept",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=uniform_button_text(decline),
                    callback_data="terms:decline",
                    style="danger",
                ),
            ]
        ]
    )


def cancel_keyboard(language: str) -> InlineKeyboardMarkup:
    text = uniform_button_text(
        translate(language, "Отмена", "Cancel", "Жокко чыгаруу")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data="flow:cancel",
                    style="danger",
                )
            ]
        ]
    )


def platforms_keyboard(flow: str, language: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, platform in enumerate(settings.platforms):
        if not database.is_platform_enabled(platform):
            continue
        current.append(
            InlineKeyboardButton(
                text=platform_button_text(platform),
                icon_custom_emoji_id=platform_button_icon(platform),
                callback_data=f"platform:{flow}:{index}",
                style="success",
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(cancel_keyboard(language).inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_choice_keyboard(account_id: str, language: str) -> InlineKeyboardMarkup:
    use_text = translate(
        language,
        f"Использовать сохранённый ID: {account_id}",
        f"Use saved ID: {account_id}",
        f"Сакталган ID колдонуу: {account_id}",
    )
    new_text = translate(language, "Другой ID", "Enter another ID", "Башка ID")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uniform_button_text(use_text),
                    callback_data="account:use",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=uniform_button_text(new_text),
                    callback_data="account:new",
                    style="success",
                )
            ],
            cancel_keyboard(language).inline_keyboard[0],
        ]
    )


def amount_keyboard(flow: str, language: str) -> InlineKeyboardMarkup:
    values = (
        (100, 200, 500, 1000, 2000, 5000, 10000)
        if flow == "deposit"
        else (500, 1000, 2000, 5000, 10000)
    )
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for value in values:
        current.append(
            InlineKeyboardButton(
                text=uniform_button_text(f"{value:,}".replace(",", " ")),
                callback_data=f"amount:{flow}:{value}",
                style="success",
            )
        )
        if len(current) == 3:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(cancel_keyboard(language).inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_keyboard(language: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, method in enumerate(settings.payment_methods):
        if not database.is_payment_method_enabled(method.key):
            continue
        current.append(
            InlineKeyboardButton(
                text=uniform_button_text(method.name),
                callback_data=f"payment:{index}",
                style="success",
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(cancel_keyboard(language).inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_action_keyboard(
    method: PaymentMethod,
    language: str,
    data: dict | None = None,
    total_minor: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if method.url:
        url = method.url
        if data is not None and total_minor is not None:
            try:
                url = render_payment_template(method.url, data, total_minor)
            except (KeyError, ValueError):
                logging.exception("Invalid payment URL template for %s", method.key)
        text = translate(
            language,
            f"Открыть {method.name}",
            f"Open {method.name}",
            f"{method.name} ачуу",
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=uniform_button_text(text),
                    url=url,
                    style="success",
                )
            ]
        )
    smart_url = smart_bank_url(method)
    if smart_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=uniform_button_text(
                        translate(
                            language,
                            f"Открыть {method.name}",
                            f"Open {method.name}",
                            f"{method.name} ачуу",
                        )
                    ),
                    url=smart_url,
                    style="success",
                )
            ]
        )
    else:
        app_buttons: list[InlineKeyboardButton] = []
        if method.android_url:
            app_buttons.append(
                InlineKeyboardButton(
                    text=uniform_button_text("🤖 Android"),
                    url=method.android_url,
                    style="success",
                )
            )
        if method.ios_url:
            app_buttons.append(
                InlineKeyboardButton(
                    text=uniform_button_text("🍎 iPhone"),
                    url=method.ios_url,
                    style="success",
                )
            )
        if app_buttons:
            rows.append(app_buttons)
    rows.append(cancel_keyboard(language).inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smart_bank_url(method: PaymentMethod) -> str | None:
    if (
        settings.smart_link_base_url
        and method.android_url
        and method.ios_url
    ):
        return f"{settings.smart_link_base_url}/open/{method.key}"
    return None


def payment_method_by_key(key: str) -> PaymentMethod | None:
    return next(
        (item for item in settings.payment_methods if item.key == key.upper()),
        None,
    )


async def smart_bank_redirect(request: web.Request) -> web.StreamResponse:
    method = payment_method_by_key(request.match_info["bank"])
    if method is None or not method.android_url or not method.ios_url:
        raise web.HTTPNotFound(text="Bank application link is not configured.")

    user_agent = request.headers.get("User-Agent", "").lower()
    if any(device in user_agent for device in ("iphone", "ipad", "ipod")):
        raise web.HTTPFound(method.ios_url)
    if "android" in user_agent:
        raise web.HTTPFound(method.android_url)

    bank_name = html.escape(method.name)
    android_url = html.escape(method.android_url, quote=True)
    ios_url = html.escape(method.ios_url, quote=True)
    return web.Response(
        text=(
            "<!doctype html><html lang='ru'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{bank_name}</title>"
            "<style>body{font-family:system-ui;text-align:center;padding:48px 16px;}"
            "a{display:block;max-width:320px;margin:14px auto;padding:16px;"
            "border-radius:12px;background:#79cf6d;color:#fff;text-decoration:none;}"
            "</style>"
            f"<h2>Открыть {bank_name}</h2>"
            f"<a href='{android_url}'>🤖 Android</a>"
            f"<a href='{ios_url}'>🍎 iPhone</a></html>"
        ),
        content_type="text/html",
    )


async def healthcheck(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_smart_link_server() -> web.AppRunner | None:
    raw_port = os.getenv("PORT", "").strip()
    if not raw_port:
        return None
    try:
        port = int(raw_port)
    except ValueError:
        logging.warning("PORT must be an integer; smart bank links are disabled")
        return None

    app = web.Application()
    app.router.add_get("/health", healthcheck)
    app.router.add_get("/open/{bank}", smart_bank_redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logging.info("Smart bank-link server is listening on port %s", port)
    return runner


def payment_total_minor(amount_minor: int) -> int:
    # Deposits are paid without an added fee. Keep the amount in whole soms so
    # the message, QR code and bank link all contain the same exact value.
    return int(
        (Decimal(amount_minor) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        * Decimal(100)
    )


def amount_for_url(amount_minor: int) -> str:
    value = Decimal(amount_minor) / Decimal(100)
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_payment_template(
    template: str,
    data: dict,
    total_minor: int,
) -> str:
    return template.format(
        amount=amount_for_url(total_minor),
        original_amount=amount_for_url(int(data["amount_minor"])),
        account_id=quote(str(data["account_id"]), safe=""),
        platform=quote(str(data["platform"]), safe=""),
    )


def bank_links_keyboard(
    data: dict,
    total_minor: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for method in settings.payment_methods:
        if not database.is_payment_method_enabled(method.key):
            continue
        try:
            payment_url = (
                render_payment_template(method.url, data, total_minor)
                if method.url
                else None
            )
        except (KeyError, ValueError):
            logging.exception("Invalid payment URL template for %s", method.key)
            payment_url = None
        button_kwargs: dict[str, str] = (
            {"url": payment_url}
            if payment_url
            else {"callback_data": f"bank:select:{method.key}"}
        )
        current.append(
            InlineKeyboardButton(
                text=uniform_button_text(method.name),
                style="success",
                **button_kwargs,
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(cancel_keyboard(language).inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def generate_qr_from_payload(
    payload: str,
    filename: str = "payment_qr.png",
) -> BufferedInputFile:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BufferedInputFile(buffer.getvalue(), filename=filename)


def generate_payment_qr(method: PaymentMethod) -> BufferedInputFile:
    if not method.url:
        raise ValueError("Payment URL is not configured")
    safe_name = re.sub(r"[^a-z0-9]+", "_", method.name.lower()).strip("_")
    return generate_qr_from_payload(
        method.url,
        f"{safe_name or 'payment'}_qr.png",
    )


def cancel_payment_timer(user_id: int) -> None:
    task = payment_expiry_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()


async def expire_payment(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    payment_message_id: int,
) -> None:
    try:
        await asyncio.sleep(settings.payment_timeout_seconds)
        if await state.get_state() != DepositFlow.proof.state:
            return
        data = await state.get_data()
        if int(data.get("payment_message_id", 0)) != payment_message_id:
            return
        database.release_payment_reservation(user_id)
        await state.clear()
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=payment_message_id,
                reply_markup=None,
            )
        except Exception:
            logging.exception("Не удалось убрать просроченные платёжные кнопки")
        language = get_language(user_id)
        await bot.send_message(
            chat_id,
            translate(
                language,
                "⏰ Пополнение отменено, время оплаты прошло.\n\n❌ Не переводите по старым реквизитам.\n\nНачните заново, нажав «Пополнить».",
                "⏰ Deposit cancelled: payment time has expired.\n\n❌ Do not transfer money using the old payment details.\n\nStart again by pressing Deposit.",
                "⏰ Толуктоо жокко чыгарылды, төлөө убактысы бүттү.\n\n❌ Эски реквизиттерге акча которбоңуз.\n\n«Толуктоо» баскычын басып кайра баштаңыз.",
            ),
            reply_markup=main_keyboard(language),
        )
    except asyncio.CancelledError:
        return
    finally:
        current = payment_expiry_tasks.get(user_id)
        if current is asyncio.current_task():
            payment_expiry_tasks.pop(user_id, None)


def admin_keyboard(operation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uniform_button_text("Подтвердить"),
                    callback_data=f"operation:approve:{operation_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=uniform_button_text("Отклонить"),
                    callback_data=f"operation:reject:{operation_id}",
                    style="danger",
                ),
            ]
        ]
    )


def referral_keyboard(language: str) -> InlineKeyboardMarkup:
    text = translate(
        language,
        "Вывести реф. баланс",
        "Withdraw referral balance",
        "Рефералдык балансты чыгаруу",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uniform_button_text(text),
                    callback_data="referral:withdraw",
                    style="success",
                )
            ]
        ]
    )


def format_money(amount_minor: int) -> str:
    if amount_minor % 100 == 0:
        amount = f"{amount_minor // 100:,}".replace(",", " ")
    else:
        amount = f"{Decimal(amount_minor) / Decimal(100):,.2f}".replace(",", " ")
    return f"{amount} {settings.currency}"


def parse_money(text: str) -> int | None:
    normalized = (
        strip_uniform_button_icon(text).replace(" ", "").replace(",", ".")
    )
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", normalized):
        return None
    try:
        value = Decimal(normalized).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
            return None
        return int(value * 100)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def operation_kind(operation: Operation, language: str = "ru") -> str:
    if operation.source == "referral":
        return translate(
            language,
            "Вывод реф. баланса",
            "Referral withdrawal",
            "Рефералдык балансты чыгаруу",
        )
    if operation.kind == "deposit":
        return translate(language, "Пополнение", "Deposit", "Толуктоо")
    return translate(language, "Вывод", "Withdrawal", "Чыгаруу")


def operation_status(status: str, language: str = "ru") -> str:
    values = {
        "ru": {
            "pending": "⏳ ожидает",
            "approved": "✅ подтверждена",
            "rejected": "❌ отклонена",
        },
        "en": {
            "pending": "⏳ pending",
            "approved": "✅ approved",
            "rejected": "❌ rejected",
        },
        "ky": {
            "pending": "⏳ күтүлүүдө",
            "approved": "✅ ырасталды",
            "rejected": "❌ четке кагылды",
        },
    }
    return values.get(language, values["ru"])[status]


async def send_language_choice(message: Message) -> None:
    await message.answer(
        "Выберите язык / Choose your language / Тилди тандаңыз:",
        reply_markup=language_keyboard(),
    )


async def send_terms(
    message: Message,
    language: str,
    require_acceptance: bool,
) -> None:
    custom_offer = database.get_setting("offer_text", "").strip()
    text = (
        html.escape(custom_offer)
        if custom_offer
        else translate(language, TERMS_RU, TERMS_EN, TERMS_KY)
    )
    markup = agreement_keyboard(language) if require_acceptance else None
    await message.answer(text, reply_markup=markup)


async def ensure_ready(message: Message, bot: Bot) -> UserProfile | None:
    if message.from_user is None:
        return None
    register_user(message.from_user)
    profile = get_profile(message.from_user.id)
    if profile is None:
        return None
    if not await require_subscription(message, message.from_user, bot):
        return None
    if profile.language not in SUPPORTED_LANGUAGES:
        database.set_language(message.from_user.id, "ru")
        profile = get_profile(message.from_user.id)
    if not settings.admin_ids:
        await message.answer(
            localize(
                message.from_user.id,
                "⚠️ Администратор ещё не настроен. Отправьте /id и добавьте "
                "полученное число в ADMIN_IDS файла .env.",
                "⚠️ The administrator is not configured yet. Send /id and add "
                "the returned number to ADMIN_IDS in the .env file.",
            )
        )
        return None
    return profile


async def show_main_menu(message: Message, user: User) -> None:
    language = get_language(user.id)
    operator = (
        database.get_setting("support_contact", "").strip()
        or settings.support_username
        or "не настроен"
    )
    if language == "en":
        greeting = (
            f"<b>💳 {html.escape(settings.service_name)}</b>\n\n"
            f"Hello, {html.escape(user.first_name)}!\n\n"
            "<b>Deposits and withdrawals 🇰🇬</b>\n\n"
            "✂️ 0% commission\n🛡️ Protected transactions\n"
            "🚀 Processing: 10 sec – 1 min\n\n"
            f"👨‍💼 Support: {html.escape(operator)}\n\n"
            "<b>Working 24/7 💯</b>"
        )
    elif language == "ky":
        greeting = (
            f"Салам, {html.escape(user.first_name)} | "
            f"<b>{html.escape(settings.service_name)}</b>! 🟢\n\n"
            "🟢 Толуктоо | Чыгаруу\n\n"
            "🛬 Толуктоо — 0%\n"
            "🛫 Чыгаруу — 0%\n"
            "🌐 24/7 иштейбиз\n\n"
            f"👨‍💼 Оператор: {html.escape(operator)}\n\n"
            "🛡️ Каржылык көзөмөлдү өзүнчө коопсуздук бөлүмү камсыздайт."
        )
    else:
        greeting = (
            f"<b>💳 {html.escape(settings.service_name)}</b>\n\n"
            f"Привет, {html.escape(user.first_name)}! 💬\n\n"
            "<b>Пополнение и выводы 🇰🇬</b>\n\n"
            "✂️ 0% комиссии\n🛡️ Защищённые транзакции\n"
            "🚀 Обработка: 10 сек – 1 мин\n\n"
            f"👨‍💼 Служба поддержки: {html.escape(operator)}\n\n"
            "<b>Работаем 24/7! 💯</b>"
        )
    await message.answer(greeting, reply_markup=main_keyboard(language))


def admin_operation_text(operation: Operation, user: User | None = None) -> str:
    username = ""
    if user is not None:
        username = f"\nПользователь: {html.escape(user.full_name)}"
        if user.username:
            username += f" (@{html.escape(user.username)})"
    details = []
    if operation.platform:
        details.append(f"Платформа: <b>{html.escape(operation.platform)}</b>")
    if operation.platform_account_id:
        details.append(
            f"Игровой ID: <code>{html.escape(operation.platform_account_id)}</code>"
        )
    if operation.payment_method:
        details.append(
            f"Способ оплаты: <b>{html.escape(operation.payment_method)}</b>"
        )
    if operation.card_id:
        card = database.get_card(operation.card_id)
        if card is not None:
            details.append(
                f"Карта: <b>{html.escape(card.bank_name)}</b> "
                f"<code>•••• {html.escape(card.card_number[-4:])}</code>"
            )
    if operation.kind == "deposit" and operation.payment_total_minor:
        details.append(
            "Точная сумма перевода: "
            f"<b>{format_money(operation.payment_total_minor)}</b>"
        )
    if operation.payout_details:
        details.append(
            f"Реквизиты: <code>{html.escape(operation.payout_details)}</code>"
        )
    if operation.request_code:
        details.append(
            f"Код/подтверждение: <code>{html.escape(operation.request_code)}</code>"
        )
    details_text = "\n".join(details)
    if details_text:
        details_text = f"\n{details_text}"
    return (
        f"<b>{operation_kind(operation)} #{operation.id}</b>"
        f"{username}\nTelegram ID: <code>{operation.user_id}</code>"
        f"\nСумма: <b>{format_money(operation.amount_minor)}</b>"
        f"{details_text}\nСтатус: {operation_status(operation.status)}"
    )


async def send_operation_media(
    bot: Bot,
    chat_id: int,
    file_id: str,
    file_type: str | None,
    caption: str,
) -> None:
    if file_type == "photo":
        await bot.send_photo(chat_id, file_id, caption=caption)
    else:
        await bot.send_document(chat_id, file_id, caption=caption)


async def notify_admins(bot: Bot, operation: Operation, user: User) -> None:
    text = admin_operation_text(operation, user)
    staff_ids = {
        int(member["user_id"]) for member in database.list_staff()
    } or set(settings.admin_ids)
    for admin_id in staff_ids:
        try:
            if operation.kind == "deposit" and operation.proof_file_id:
                if operation.proof_type == "photo":
                    await bot.send_photo(
                        admin_id,
                        operation.proof_file_id,
                        caption=text,
                        reply_markup=admin_keyboard(operation.id),
                    )
                else:
                    await bot.send_document(
                        admin_id,
                        operation.proof_file_id,
                        caption=text,
                        reply_markup=admin_keyboard(operation.id),
                    )
            else:
                await bot.send_message(
                    admin_id,
                    text,
                    reply_markup=admin_keyboard(operation.id),
                )
                if operation.payout_file_id:
                    await send_operation_media(
                        bot,
                        admin_id,
                        operation.payout_file_id,
                        operation.payout_file_type,
                        f"Реквизиты для заявки #{operation.id}",
                    )
                if operation.proof_file_id:
                    await send_operation_media(
                        bot,
                        admin_id,
                        operation.proof_file_id,
                        operation.proof_type,
                        f"Подтверждение заявки #{operation.id}",
                    )
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin_id)


async def notify_payment_expected(
    bot: Bot,
    user: User,
    data: dict,
    requested_amount_minor: int,
    payment_total_minor: int,
) -> None:
    username = f" (@{html.escape(user.username)})" if user.username else ""
    text = (
        "🟡 <b>Ожидается перевод</b>\n"
        f"Игрок: {html.escape(user.full_name)}{username}\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"Платформа: <b>{html.escape(str(data['platform']))}</b>\n"
        f"Игровой ID: <code>{html.escape(str(data['account_id']))}</code>\n"
        f"Сумма зачисления: <b>{format_money(requested_amount_minor)}</b>\n"
        f"Точная сумма перевода: <b>{format_money(payment_total_minor)}</b>\n"
        f"Код платежа: <b>{payment_total_minor % 100:02d} коп.</b>\n"
        f"Резерв: {settings.payment_timeout_seconds // 60} минут"
    )
    staff_ids = {
        int(member["user_id"]) for member in database.list_staff()
    } or set(settings.admin_ids)
    for admin_id in staff_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logging.exception(
                "Не удалось отправить ожидаемый платёж администратору %s",
                admin_id,
            )


async def begin_platform_flow(
    message: Message,
    state: FSMContext,
    flow: str,
    user_id: int | None = None,
) -> None:
    if user_id is None and message.from_user is None:
        return
    resolved_user_id = user_id if user_id is not None else message.from_user.id
    language = get_language(resolved_user_id)
    await state.clear()
    if not any(database.is_platform_enabled(item) for item in settings.platforms):
        await message.answer(
            translate(
                language,
                "Сейчас все игровые платформы временно отключены. Попробуйте позже.",
                "All gaming platforms are temporarily disabled. Try again later.",
                "Учурда бардык оюн платформалары убактылуу өчүрүлгөн. Кийинчерээк аракет кылыңыз.",
            )
        )
        return
    if flow == "deposit":
        await state.set_state(DepositFlow.platform)
        title = translate(
            language,
            "📥 <b>Пополнение</b>\n\nВыберите игровую платформу:",
            "📥 <b>Deposit</b>\n\nSelect a gaming platform:",
            "📥 <b>Толуктоо</b>\n\nОюн платформасын тандаңыз:",
        )
    elif flow == "withdrawal":
        await state.set_state(WithdrawalFlow.platform)
        title = translate(
            language,
            "📤 <b>Вывод средств</b>\n\nВыберите платформу, на которой вы создали заявку на вывод:",
            "📤 <b>Withdrawal</b>\n\nSelect the platform where you created the withdrawal request:",
            "📤 <b>Каражат чыгаруу</b>\n\nЧыгаруу өтүнмөсүн түзгөн платформаңызды тандаңыз:",
        )
    else:
        await state.set_state(ReferralFlow.platform)
        title = translate(
            language,
            "Выберите платформу для зачисления реферального баланса:",
            "Select the platform account for the referral payout:",
            "Рефералдык балансты которуу үчүн платформаны тандаңыз:",
        )
    await state.update_data(flow=flow)
    await message.answer(
        title,
        reply_markup=platforms_reply_keyboard(language),
    )


async def ask_for_account_id(
    message: Message,
    state: FSMContext,
    user_id: int,
    flow: str,
    platform: str,
) -> None:
    language = get_language(user_id)
    saved = database.get_platform_account(user_id, platform)
    await state.update_data(
        flow=flow,
        platform=platform,
        saved_account_id=saved,
    )
    state_value = {
        "deposit": DepositFlow.account_id,
        "withdrawal": WithdrawalFlow.account_id,
        "referral": ReferralFlow.account_id,
    }[flow]
    await state.set_state(state_value)
    if saved:
        text = translate(
            language,
            f"Выбранная платформа: <b>{html.escape(platform)}</b>\nИспользовать сохранённый игровой ID или ввести другой?",
            f"Selected platform: <b>{html.escape(platform)}</b>\nUse the saved gaming ID or enter another one.",
            f"Тандалган платформа: <b>{html.escape(platform)}</b>\nСакталган оюн ID-син колдоносузбу же башкасын киргизесизби?",
        )
        await message.answer(
            translate(
                language,
                "✅ ID найден в профиле.",
                "✅ Saved ID found.",
                "✅ Профилден сакталган ID табылды.",
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer(
            text,
            reply_markup=account_choice_keyboard(saved, language),
        )
    else:
        text = translate(
            language,
            f"Выбранная платформа: <b>{html.escape(platform)}</b>\n\nВведите ID вашего игрового аккаунта:",
            f"Selected platform: <b>{html.escape(platform)}</b>\n\nEnter your gaming account ID:",
            f"Тандалган платформа: <b>{html.escape(platform)}</b>\n\nОюн аккаунтуңуздун ID-син киргизиңиз:",
        )
        await message.answer(text, reply_markup=ReplyKeyboardRemove())


async def account_selected(
    message: Message,
    state: FSMContext,
    bot: Bot,
    user: User,
    account_id: str,
) -> None:
    data = await state.get_data()
    flow = str(data["flow"])
    platform = str(data["platform"])
    database.save_platform_account(user.id, platform, account_id)
    await state.update_data(account_id=account_id)
    language = get_language(user.id)

    if flow == "referral":
        operation = database.create_referral_withdrawal(
            user.id,
            platform,
            account_id,
            settings.min_referral_withdraw_minor,
        )
        await state.clear()
        if operation is None:
            await message.answer(
                localize(
                    user.id,
                    "Недостаточно реферальных средств для вывода.",
                    "Insufficient referral balance for withdrawal.",
                ),
                reply_markup=main_keyboard(language),
            )
            return
        await notify_admins(bot, operation, user)
        await message.answer(
            localize(
                user.id,
                f"Заявка <b>#{operation.id}</b> на вывод реферального "
                f"баланса {format_money(operation.amount_minor)} создана.",
                f"Referral withdrawal request <b>#{operation.id}</b> for "
                f"{format_money(operation.amount_minor)} has been created.",
            ),
            reply_markup=main_keyboard(language),
        )
        return

    if flow == "deposit":
        await state.set_state(DepositFlow.amount)
        minimum = settings.min_deposit_minor
        maximum = settings.max_deposit_minor
        saved_account_id = str(data.get("saved_account_id") or "")
        await message.answer(
            translate(
                language,
                (
                    "✅ ID найден в профиле."
                    if account_id == saved_account_id
                    else "✅ Игровой ID принят. Продолжаем…"
                ),
                (
                    "✅ ID found in your profile."
                    if account_id == saved_account_id
                    else "✅ Gaming ID accepted. Continuing…"
                ),
                (
                    "✅ ID профилден табылды."
                    if account_id == saved_account_id
                    else "✅ Оюн ID кабыл алынды. Улантабыз…"
                ),
            )
        )
        prompt = translate(
            language,
            f"Игровой ID: <code>{html.escape(account_id)}</code>\nМинимум: <b>{format_money(minimum)}</b>\nМаксимум: <b>{format_money(maximum)}</b>\n\nВведите или выберите сумму пополнения:",
            f"Gaming ID: <code>{html.escape(account_id)}</code>\nMinimum: <b>{format_money(minimum)}</b>\nMaximum: <b>{format_money(maximum)}</b>\n\nEnter or select the deposit amount:",
            f"Оюн ID: <code>{html.escape(account_id)}</code>\nМинимум: <b>{format_money(minimum)}</b>\nМаксимум: <b>{format_money(maximum)}</b>\n\nТолуктоо суммасын киргизиңиз же тандаңыз:",
        )
        await message.answer(
            prompt,
            reply_markup=amount_reply_keyboard("deposit", language),
        )
    else:
        await state.set_state(WithdrawalFlow.amount)
        minimum = settings.min_withdraw_minor
        maximum = settings.max_withdraw_minor
        prompt = translate(
            language,
            f"Игровой ID: <code>{html.escape(account_id)}</code>\nМинимум: <b>{format_money(minimum)}</b>\nМаксимум: <b>{format_money(maximum)}</b>\n\nВведите или выберите сумму вывода:",
            f"Gaming ID: <code>{html.escape(account_id)}</code>\nMinimum: <b>{format_money(minimum)}</b>\nMaximum: <b>{format_money(maximum)}</b>\n\nEnter or select the withdrawal amount:",
            f"Оюн ID: <code>{html.escape(account_id)}</code>\nМинимум: <b>{format_money(minimum)}</b>\nМаксимум: <b>{format_money(maximum)}</b>\n\nЧыгаруу суммасын киргизиңиз же тандаңыз:",
        )
        await message.answer(
            prompt,
            reply_markup=amount_reply_keyboard("withdrawal", language),
        )


async def accept_deposit_amount(
    message: Message,
    state: FSMContext,
    bot: Bot,
    user_id: int,
    amount_minor: int,
) -> None:
    language = get_language(user_id)
    if not settings.min_deposit_minor <= amount_minor <= settings.max_deposit_minor:
        await message.answer(
            localize(
                user_id,
                f"Сумма должна быть от {format_money(settings.min_deposit_minor)} "
                f"до {format_money(settings.max_deposit_minor)}.",
                f"Amount must be between {format_money(settings.min_deposit_minor)} "
                f"and {format_money(settings.max_deposit_minor)}.",
                f"Сумма {format_money(settings.min_deposit_minor)} менен "
                f"{format_money(settings.max_deposit_minor)} аралыгында болушу керек.",
            )
        )
        return
    if amount_minor % 100 != 0:
        await message.answer(
            localize(
                user_id,
                "Введите сумму целым числом без копеек, например 1000. "
                "Уникальные копейки бот добавит автоматически.",
                "Enter a whole amount without cents, for example 1000. "
                "The bot will add unique cents automatically.",
                "Сумманы тыйынсыз бүтүн сан менен киргизиңиз, мисалы 1000. "
                "Бот уникалдуу тыйындарды автоматтык түрдө кошот.",
            )
        )
        return
    whole_total_minor = payment_total_minor(amount_minor)
    try:
        total_minor = database.reserve_payment_amount(
            user_id=user_id,
            requested_amount_minor=amount_minor,
            whole_amount_minor=whole_total_minor,
            ttl_seconds=settings.payment_timeout_seconds,
        )
    except RuntimeError:
        await message.answer(
            localize(
                user_id,
                "Сейчас все коды платежей заняты. Попробуйте снова через 5 минут.",
                "All payment codes are busy. Please try again in 5 minutes.",
                "Учурда бардык төлөм коддору бош эмес. 5 мүнөттөн кийин кайра аракет кылыңыз.",
            )
        )
        return
    await state.update_data(
        amount_minor=amount_minor,
        payment_total_minor=total_minor,
        payment_method="QR / bank link",
    )
    await state.set_state(DepositFlow.proof)
    data = await state.get_data()

    await message.answer(
        translate(language, "Генерация QR...", "Generating QR...", "QR түзүлүүдө..."),
        reply_markup=ReplyKeyboardRemove(),
    )

    caption = translate(
        language,
        f"📌 <b>Оплатите точную сумму:</b> <b>{format_money(total_minor)}</b>\n🎮 {html.escape(str(data['platform']))} ID: <code>{html.escape(str(data['account_id']))}</code>\n\n⚠️ После оплаты отправьте скриншот или файл чека.\n\n⏳ Время на оплату: <b>{settings.payment_timeout_seconds // 60:02d}:00</b>\n\nВыберите банк кнопкой под QR-кодом.",
        f"📌 <b>Pay the exact amount:</b> <b>{format_money(total_minor)}</b>\n🎮 {html.escape(str(data['platform']))} ID: <code>{html.escape(str(data['account_id']))}</code>\n\n⚠️ After payment, send a receipt screenshot or file.\n\n⏳ Payment time: <b>{settings.payment_timeout_seconds // 60:02d}:00</b>\n\nChoose a bank using a button below.",
        f"📌 <b>Так сумманы төлөңүз:</b> <b>{format_money(total_minor)}</b>\n🎮 {html.escape(str(data['platform']))} ID: <code>{html.escape(str(data['account_id']))}</code>\n\n⚠️ Төлөгөндөн кийин чектин скриншотун же файлын жөнөтүңүз.\n\n⏳ Төлөө убактысы: <b>{settings.payment_timeout_seconds // 60:02d}:00</b>\n\nQR-коддун астындагы баскыч менен банкты тандаңыз.",
    )
    caption += translate(
        language,
        f"\n\n🔢 Код платежа: <b>{total_minor % 100:02d} коп.</b> Переведите точную сумму вместе с копейками.",
        f"\n\n🔢 Payment code: <b>{total_minor % 100:02d}</b>. Transfer the exact amount including the cents.",
        f"\n\n🔢 Төлөм коду: <b>{total_minor % 100:02d} тыйын.</b> Тыйындары менен так сумманы которуңуз.",
    )
    markup = bank_links_keyboard(data, total_minor, language)
    photo: str | FSInputFile | BufferedInputFile | None = None
    has_managed_cards = bool(database.list_cards())
    if not has_managed_cards and settings.payment_qr_image:
        if Path(settings.payment_qr_image).is_file():
            photo = FSInputFile(settings.payment_qr_image)
        elif re.fullmatch(r"[A-Za-z0-9_-]{20,}", settings.payment_qr_image) or re.match(
            r"^https?://", settings.payment_qr_image, flags=re.IGNORECASE
        ):
            photo = settings.payment_qr_image
        else:
            logging.warning(
                "PAYMENT_QR_IMAGE file is missing: %s", settings.payment_qr_image
            )
    elif not has_managed_cards and settings.payment_qr_url_template:
        try:
            payload = render_payment_template(
                settings.payment_qr_url_template,
                data,
                total_minor,
            )
            photo = generate_qr_from_payload(payload)
        except (KeyError, ValueError):
            logging.exception("Неверный PAYMENT_QR_URL_TEMPLATE")

    if photo is not None:
        try:
            sent = await message.answer_photo(
                photo,
                caption=caption,
                reply_markup=markup,
            )
        except Exception:
            logging.exception("Не удалось отправить единый QR-код")
            sent = await message.answer(
                "⚠️ <b>QR-код временно недоступен.</b>\n\n" + caption,
                reply_markup=markup,
            )
    else:
        prefix = (
            ""
            if has_managed_cards
            else "⚠️ <b>QR-код ещё не настроен администратором.</b>\n\n"
        )
        sent = await message.answer(
            prefix + caption,
            reply_markup=markup,
        )
    await state.update_data(payment_message_id=sent.message_id)
    if message.from_user is not None:
        await notify_payment_expected(
            bot,
            message.from_user,
            data,
            amount_minor,
            total_minor,
        )
    cancel_payment_timer(user_id)
    payment_expiry_tasks[user_id] = asyncio.create_task(
        expire_payment(
            bot,
            state,
            user_id,
            message.chat.id,
            sent.message_id,
        )
    )


async def accept_withdrawal_amount(
    message: Message,
    state: FSMContext,
    user_id: int,
    amount_minor: int,
) -> None:
    language = get_language(user_id)
    if not settings.min_withdraw_minor <= amount_minor <= settings.max_withdraw_minor:
        await message.answer(
            localize(
                user_id,
                f"Сумма должна быть от {format_money(settings.min_withdraw_minor)} "
                f"до {format_money(settings.max_withdraw_minor)}.",
                f"Amount must be between {format_money(settings.min_withdraw_minor)} "
                f"and {format_money(settings.max_withdraw_minor)}.",
                f"Сумма {format_money(settings.min_withdraw_minor)} менен "
                f"{format_money(settings.max_withdraw_minor)} аралыгында болушу керек.",
            )
        )
        return
    await state.update_data(amount_minor=amount_minor)
    await state.set_state(WithdrawalFlow.payout)
    await message.answer(
        localize(
            user_id,
            "Отправьте реквизиты для выплаты текстом или фотографию QR-кода "
            "вашего кошелька.\n\n<b>Не отправляйте пароль, PIN или CVV.</b>",
            "Send payout details as text or a photo of your wallet QR code.\n\n"
            "<b>Never send a password, PIN or CVV.</b>",
            "Төлөм алуу реквизиттерин текст менен же капчыгыңыздын QR-кодунун сүрөтүн жөнөтүңүз.\n\n"
            "<b>Сырсөздү, PIN же CVV кодун эч качан жөнөтпөңүз.</b>",
        ),
        reply_markup=cancel_keyboard(language),
    )


async def ask_withdrawal_evidence(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    language = get_language(user_id)
    await state.set_state(WithdrawalFlow.evidence)
    await message.answer(
        localize(
            user_id,
            "Теперь отправьте код заявки на вывод или скриншот заявки, "
            "созданной на игровой платформе.",
            "Now send the withdrawal request code or a screenshot of the request "
            "created on the gaming platform.",
        ),
        reply_markup=cancel_keyboard(language),
    )


async def finish_withdrawal(
    message: Message,
    state: FSMContext,
    bot: Bot,
    request_code: str | None,
    proof_file_id: str | None,
    proof_type: str | None,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    operation = database.create_withdrawal(
        user_id=message.from_user.id,
        amount_minor=int(data["amount_minor"]),
        platform=str(data["platform"]),
        platform_account_id=str(data["account_id"]),
        payout_details=data.get("payout_details"),
        payout_file_id=data.get("payout_file_id"),
        payout_file_type=data.get("payout_file_type"),
        request_code=request_code,
        proof_file_id=proof_file_id,
        proof_type=proof_type,
    )
    await state.clear()
    await notify_admins(bot, operation, message.from_user)
    language = get_language(message.from_user.id)
    await message.answer(
        localize(
            message.from_user.id,
            f"Заявка на вывод <b>#{operation.id}</b> создана.\n"
            f"Сумма: <b>{format_money(operation.amount_minor)}</b>\n"
            "Ожидайте проверки администратора.",
            f"Withdrawal request <b>#{operation.id}</b> has been created.\n"
            f"Amount: <b>{format_money(operation.amount_minor)}</b>\n"
            "Please wait for administrator verification.",
        ),
        reply_markup=main_keyboard(language),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    await state.clear()
    cancel_payment_timer(message.from_user.id)
    database.release_payment_reservation(message.from_user.id)
    register_user(message.from_user)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].isdigit():
        database.set_referrer(message.from_user.id, int(parts[1]))

    profile = get_profile(message.from_user.id)
    if profile is None:
        return
    await send_language_choice(message)


@router.message(Command("language"))
@router.message(
    F.text.in_(
        {
            MAIN_LABELS["language"]["ru"],
            MAIN_LABELS["language"]["en"],
            MAIN_LABELS["language"]["ky"],
            YELLOW_MAIN_LABELS["language"]["ru"],
            YELLOW_MAIN_LABELS["language"]["en"],
            YELLOW_MAIN_LABELS["language"]["ky"],
            LEGACY_MAIN_LABELS["language"]["ru"],
            LEGACY_MAIN_LABELS["language"]["en"],
            LEGACY_MAIN_LABELS["language"]["ky"],
        }
    )
)
async def language_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is not None:
        register_user(message.from_user)
    await send_language_choice(message)


@router.callback_query(F.data.startswith("language:"))
async def language_callback(callback: CallbackQuery, bot: Bot) -> None:
    language = (callback.data or "").split(":", 1)[-1]
    if language not in SUPPORTED_LANGUAGES:
        await callback.answer("Invalid language", show_alert=True)
        return
    register_user(callback.from_user)
    database.set_language(callback.from_user.id, language)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            translate(
                language,
                "Язык выбран: Русский",
                "Language: English",
                "Тандалган тил: Кыргызча",
            )
        )
        profile = get_profile(callback.from_user.id)
        if profile and profile.accepted_terms_at:
            if await require_subscription(callback.message, callback.from_user, bot):
                await show_main_menu(callback.message, callback.from_user)
        else:
            await send_terms(callback.message, language, True)


@router.callback_query(F.data.startswith("terms:"))
async def terms_callback(callback: CallbackQuery, bot: Bot) -> None:
    action = (callback.data or "").split(":", 1)[-1]
    language = get_language(callback.from_user.id)
    if action == "decline":
        await callback.answer(
            translate(
                language,
                "Для использования сервиса необходимо принять правила.",
                "Agreement is required to use the service.",
                "Сервисти колдонуу үчүн эрежелерди кабыл алуу керек.",
            ),
            show_alert=True,
        )
        return
    database.accept_terms(callback.from_user.id)
    await callback.answer(
        translate(language, "Условия приняты", "Accepted", "Шарттар кабыл алынды")
    )
    if callback.message is not None:
        if not await require_subscription(callback.message, callback.from_user, bot):
            return
        await callback.message.answer(
            translate(
                language,
                "✅ Условия приняты. Личный кабинет готов к работе.",
                "✅ Agreement accepted. Your account is ready.",
                "✅ Шарттар кабыл алынды. Жеке кабинет колдонууга даяр.",
            ),
            reply_markup=main_keyboard(language),
        )
        await show_main_menu(callback.message, callback.from_user)


@router.callback_query(F.data == "subscription:check")
async def subscription_check_callback(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None:
        return
    if not await has_required_subscription(bot, callback.from_user.id):
        await callback.answer("Подписка пока не найдена. Подпишитесь и попробуйте снова.", show_alert=True)
        return
    await callback.answer("Подписка подтверждена")
    profile = get_profile(callback.from_user.id)
    if profile and profile.accepted_terms_at:
        await show_main_menu(callback.message, callback.from_user)
    else:
        await send_terms(callback.message, get_language(callback.from_user.id), True)


@router.message(Command("id"))
async def show_id(message: Message) -> None:
    if message.from_user is None:
        return
    register_user(message.from_user)
    await message.answer(
        f"Ваш Telegram ID / Your Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


@router.message(Command("cancel"))
@router.message(
    F.text.casefold().in_(
        {
            "отмена",
            "cancel",
            "жокко чыгаруу",
            "🟨 отмена",
            "🟨 cancel",
            "🟨 жокко чыгаруу",
        }
    )
)
async def cancel(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    cancel_payment_timer(message.from_user.id)
    database.release_payment_reservation(message.from_user.id)
    await state.clear()
    language = get_language(message.from_user.id)
    await message.answer(
        translate(
            language,
            "Действие отменено. Вы вернулись в главное меню.",
            "Action cancelled. You are back in the main menu.",
            "Аракет жокко чыгарылды. Башкы менюга кайттыңыз.",
        ),
        reply_markup=main_keyboard(language),
    )


@router.callback_query(F.data == "flow:cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    cancel_payment_timer(callback.from_user.id)
    database.release_payment_reservation(callback.from_user.id)
    await state.clear()
    language = get_language(callback.from_user.id)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            translate(
                language,
                "Действие отменено. Вы вернулись в главное меню.",
                "Action cancelled. You are back in the main menu.",
                "Аракет жокко чыгарылды. Башкы менюга кайттыңыз.",
            ),
            reply_markup=main_keyboard(language),
        )


@router.message(Command("deposit"))
@router.message(
    F.text.in_(
        {
            MAIN_LABELS["deposit"]["ru"],
            MAIN_LABELS["deposit"]["en"],
            MAIN_LABELS["deposit"]["ky"],
            YELLOW_MAIN_LABELS["deposit"]["ru"],
            YELLOW_MAIN_LABELS["deposit"]["en"],
            YELLOW_MAIN_LABELS["deposit"]["ky"],
            LEGACY_MAIN_LABELS["deposit"]["ru"],
            LEGACY_MAIN_LABELS["deposit"]["en"],
            LEGACY_MAIN_LABELS["deposit"]["ky"],
        }
    )
)
async def deposit_begin(message: Message, state: FSMContext, bot: Bot) -> None:
    if await ensure_ready(message, bot) is None:
        return
    await begin_platform_flow(message, state, "deposit")


@router.message(Command("withdraw"))
@router.message(
    F.text.in_(
        {
            MAIN_LABELS["withdrawal"]["ru"],
            MAIN_LABELS["withdrawal"]["en"],
            MAIN_LABELS["withdrawal"]["ky"],
            YELLOW_MAIN_LABELS["withdrawal"]["ru"],
            YELLOW_MAIN_LABELS["withdrawal"]["en"],
            YELLOW_MAIN_LABELS["withdrawal"]["ky"],
            LEGACY_MAIN_LABELS["withdrawal"]["ru"],
            LEGACY_MAIN_LABELS["withdrawal"]["en"],
            LEGACY_MAIN_LABELS["withdrawal"]["ky"],
        }
    )
)
async def withdrawal_begin(message: Message, state: FSMContext, bot: Bot) -> None:
    if await ensure_ready(message, bot) is None:
        return
    await begin_platform_flow(message, state, "withdrawal")


@router.message(
    F.text.in_(
        {
            MAIN_LABELS["support"]["ru"],
            MAIN_LABELS["support"]["en"],
            MAIN_LABELS["support"]["ky"],
            YELLOW_MAIN_LABELS["support"]["ru"],
            YELLOW_MAIN_LABELS["support"]["en"],
            YELLOW_MAIN_LABELS["support"]["ky"],
            LEGACY_MAIN_LABELS["support"]["ru"],
            LEGACY_MAIN_LABELS["support"]["en"],
            LEGACY_MAIN_LABELS["support"]["ky"],
        }
    )
)
async def support_handler(message: Message) -> None:
    if message.from_user is None:
        return
    support = (
        database.get_setting("support_contact", "").strip()
        or settings.support_username
    )
    if not support:
        await message.answer(
            localize(
                message.from_user.id,
                "Контакт техподдержки пока не настроен.",
                "The support contact is not configured yet.",
            )
        )
        return
    support_url = (
        support
        if support.lower().startswith("https://")
        else f"https://t.me/{support.lstrip('@')}"
    )
    text = (
        "Contact support"
        if get_language(message.from_user.id) == "en"
        else "Написать оператору"
    )
    await message.answer(
        localize(
            message.from_user.id,
            f"Техническая поддержка: {html.escape(support)}",
            f"Technical support: {html.escape(support)}",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=uniform_button_text(text),
                        url=support_url,
                        style="success",
                    )
                ]
            ]
        ),
    )


@router.message(
    StateFilter(
        DepositFlow.platform,
        WithdrawalFlow.platform,
        ReferralFlow.platform,
    ),
    F.text,
)
async def platform_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    platform = platform_from_button_text(message.text or "")
    if platform is None:
        await message.answer(
            localize(
                message.from_user.id,
                "Выберите платформу кнопкой ниже.",
                "Select a platform using the keyboard below.",
            )
        )
        return
    if not database.is_platform_enabled(platform):
        await message.answer("Эта платформа временно отключена.")
        return
    current_state = await state.get_state()
    flow = {
        DepositFlow.platform.state: "deposit",
        WithdrawalFlow.platform.state: "withdrawal",
        ReferralFlow.platform.state: "referral",
    }.get(current_state)
    if flow is None:
        return
    await ask_for_account_id(
        message,
        state,
        message.from_user.id,
        flow,
        platform,
    )


@router.callback_query(F.data.startswith("platform:"))
async def platform_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Invalid selection", show_alert=True)
        return
    flow = parts[1]
    try:
        index = int(parts[2])
        platform = settings.platforms[index]
    except (ValueError, IndexError):
        await callback.answer("Invalid selection", show_alert=True)
        return
    if not database.is_platform_enabled(platform):
        await callback.answer("Эта платформа временно отключена.", show_alert=True)
        return

    current_state = await state.get_state()
    expected = {
        "deposit": DepositFlow.platform.state,
        "withdrawal": WithdrawalFlow.platform.state,
        "referral": ReferralFlow.platform.state,
    }.get(flow)
    if expected is None or current_state != expected:
        await callback.answer(
            localize(
                callback.from_user.id,
                "Эта кнопка устарела. Начните действие заново.",
                "This button has expired. Start again.",
            ),
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message is not None:
        await ask_for_account_id(
            callback.message,
            state,
            callback.from_user.id,
            flow,
            platform,
        )


@router.callback_query(F.data.startswith("account:"))
async def account_choice_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    flow = data.get("flow")
    if flow not in {"deposit", "withdrawal", "referral"}:
        await callback.answer("Expired", show_alert=True)
        return
    action = (callback.data or "").split(":", 1)[-1]
    if action == "new":
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(
                localize(
                    callback.from_user.id,
                    "Введите новый ID игрового аккаунта:",
                    "Enter a new gaming account ID:",
                ),
                reply_markup=cancel_keyboard(get_language(callback.from_user.id)),
            )
        return
    saved = data.get("saved_account_id")
    if not saved:
        await callback.answer("No saved ID", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await account_selected(
            callback.message,
            state,
            bot,
            callback.from_user,
            str(saved),
        )


@router.message(
    StateFilter(
        DepositFlow.account_id,
        WithdrawalFlow.account_id,
        ReferralFlow.account_id,
    ),
    F.text,
)
async def account_id_message(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if message.from_user is None:
        return
    account_id = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,30}", account_id):
        await message.answer(
            localize(
                message.from_user.id,
                "ID должен содержать от 4 до 30 цифр или латинских символов.",
                "The ID must contain 4–30 digits or Latin characters.",
            )
        )
        return
    await account_selected(message, state, bot, message.from_user, account_id)


@router.message(DepositFlow.account_id)
@router.message(WithdrawalFlow.account_id)
@router.message(ReferralFlow.account_id)
async def account_id_invalid(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        localize(
            message.from_user.id,
            "Отправьте игровой ID обычным текстом.",
            "Send the gaming ID as a text message.",
        )
    )


@router.callback_query(F.data.startswith("amount:"))
async def amount_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Invalid amount", show_alert=True)
        return
    flow = parts[1]
    try:
        amount_minor = int(parts[2]) * 100
    except ValueError:
        await callback.answer("Invalid amount", show_alert=True)
        return
    current_state = await state.get_state()
    if flow == "deposit" and current_state == DepositFlow.amount.state:
        await callback.answer()
        if callback.message is not None:
            await accept_deposit_amount(
                callback.message,
                state,
                bot,
                callback.from_user.id,
                amount_minor,
            )
    elif flow == "withdrawal" and current_state == WithdrawalFlow.amount.state:
        await callback.answer()
        if callback.message is not None:
            await accept_withdrawal_amount(
                callback.message, state, callback.from_user.id, amount_minor
            )
    else:
        await callback.answer("Expired", show_alert=True)


@router.message(DepositFlow.amount, F.text)
async def deposit_amount_message(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if message.from_user is None:
        return
    amount = parse_money(message.text or "")
    if amount is None:
        await message.answer(
            localize(
                message.from_user.id,
                "Введите корректную сумму, например: <code>1000</code>",
                "Enter a valid amount, for example: <code>1000</code>",
            )
        )
        return
    await accept_deposit_amount(
        message,
        state,
        bot,
        message.from_user.id,
        amount,
    )


@router.message(WithdrawalFlow.amount, F.text)
async def withdrawal_amount_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    amount = parse_money(message.text or "")
    if amount is None:
        await message.answer(
            localize(
                message.from_user.id,
                "Введите корректную сумму, например: <code>1000</code>",
                "Enter a valid amount, for example: <code>1000</code>",
            )
        )
        return
    await accept_withdrawal_amount(message, state, message.from_user.id, amount)


@router.callback_query(F.data.startswith("bank:select:"))
async def bank_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != DepositFlow.proof.state:
        await callback.answer("Платёжная сессия истекла.", show_alert=True)
        return
    key = (callback.data or "").rsplit(":", 1)[-1].upper()
    method = next(
        (item for item in settings.payment_methods if item.key == key), None
    )
    if method is None or not database.is_payment_method_enabled(key):
        await callback.answer("Этот банк временно недоступен.", show_alert=True)
        return
    data = await state.get_data()
    total_minor = int(data.get("payment_total_minor", data["amount_minor"]))
    configured_cards = database.list_cards(key)
    card = database.reserve_card_for_payment(callback.from_user.id, key)
    if configured_cards and card is None:
        await callback.answer(
            "По этому банку сейчас нет карты с доступным лимитом.",
            show_alert=True,
        )
        return
    await state.update_data(
        payment_method=method.name,
        card_id=card.id if card is not None else None,
    )
    language = get_language(callback.from_user.id)
    caption = translate(
        language,
        f"🏦 <b>{html.escape(method.name)}</b>\nПлатформа: <b>{html.escape(str(data['platform']))}</b>\nИгровой ID: <code>{html.escape(str(data['account_id']))}</code>\nТочная сумма перевода: <b>{format_money(total_minor)}</b>\nКод платежа: <b>{total_minor % 100:02d} коп.</b>",
        f"🏦 <b>{html.escape(method.name)}</b>\nPlatform: <b>{html.escape(str(data['platform']))}</b>\nGaming ID: <code>{html.escape(str(data['account_id']))}</code>\nExact transfer amount: <b>{format_money(total_minor)}</b>\nPayment code: <b>{total_minor % 100:02d}</b>",
        f"🏦 <b>{html.escape(method.name)}</b>\nПлатформа: <b>{html.escape(str(data['platform']))}</b>\nОюн ID: <code>{html.escape(str(data['account_id']))}</code>\nКоторуунун так суммасы: <b>{format_money(total_minor)}</b>\nТөлөм коду: <b>{total_minor % 100:02d} тыйын.</b>",
    )
    if card is not None:
        caption += translate(
            language,
            f"\n\n💳 Реквизиты: <code>{html.escape(card.card_number)}</code>\n👤 Получатель: {html.escape(card.holder_name or 'не указан')}",
            f"\n\n💳 Payment details: <code>{html.escape(card.card_number)}</code>\n👤 Recipient: {html.escape(card.holder_name or 'not specified')}",
            f"\n\n💳 Реквизиттер: <code>{html.escape(card.card_number)}</code>\n👤 Алуучу: {html.escape(card.holder_name or 'көрсөтүлгөн эмес')}",
        )
    elif method.details:
        caption += f"\n\n{html.escape(method.details)}"
    caption += translate(
        language,
        "\n\n⚠️ Переведите точную сумму вместе с копейками.",
        "\n\n⚠️ Transfer the exact amount including cents.",
        "\n\n⚠️ Тыйындары менен так сумманы которуңуз.",
    )
    photo: str | FSInputFile | BufferedInputFile | None = None
    if method.qr:
        if Path(method.qr).is_file():
            photo = FSInputFile(method.qr)
        else:
            photo = method.qr
    elif method.url:
        try:
            photo = generate_qr_from_payload(
                render_payment_template(method.url, data, total_minor),
                f"{method.key.lower()}_payment.png",
            )
        except (KeyError, ValueError):
            logging.exception("Invalid payment URL template for %s", method.key)
    markup = payment_action_keyboard(method, language, data, total_minor)
    await callback.answer()
    if callback.message is None:
        return
    if photo is not None:
        await callback.message.answer_photo(
            photo, caption=caption, reply_markup=markup
        )
    else:
        await callback.message.answer(caption, reply_markup=markup)


@router.callback_query(F.data.startswith("payment:"))
async def payment_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != DepositFlow.payment.state:
        await callback.answer("Expired", show_alert=True)
        return
    try:
        index = int((callback.data or "").split(":", 1)[-1])
        method = settings.payment_methods[index]
    except (ValueError, IndexError):
        await callback.answer("Invalid method", show_alert=True)
        return
    if not database.is_payment_method_enabled(method.key):
        await callback.answer("Этот банк временно отключён.", show_alert=True)
        return
    data = await state.get_data()
    amount_minor = int(data.get("payment_total_minor", data["amount_minor"]))
    platform = html.escape(str(data["platform"]))
    account_id = html.escape(str(data["account_id"]))
    language = get_language(callback.from_user.id)
    await state.update_data(payment_method=method.name)
    await state.set_state(DepositFlow.proof)
    await callback.answer()
    if callback.message is None:
        return

    caption = translate(
        language,
        f"<b>{html.escape(method.name)}</b>\nПлатформа: <b>{platform}</b>\nИгровой ID: <code>{account_id}</code>\nСумма к оплате: <b>{format_money(amount_minor)}</b>\n\n{html.escape(method.details)}",
        f"<b>{html.escape(method.name)}</b>\nPlatform: <b>{platform}</b>\nGaming ID: <code>{account_id}</code>\nAmount: <b>{format_money(amount_minor)}</b>\n\n{html.escape(method.details)}",
        f"<b>{html.escape(method.name)}</b>\nПлатформа: <b>{platform}</b>\nОюн ID: <code>{account_id}</code>\nТөлөнүүчү сумма: <b>{format_money(amount_minor)}</b>\n\n{html.escape(method.details)}",
    )
    if method.url:
        caption += translate(
            language,
            "\n\nНажмите кнопку ниже, чтобы открыть банк, или отсканируйте QR-код.",
            "\n\nUse the button below to open the bank, or scan the QR code.",
            "\n\nБанкты ачуу үчүн төмөнкү баскычты басыңыз же QR-кодду скандаңыз.",
        )
    photo: str | FSInputFile | BufferedInputFile | None = None
    if method.qr:
        if Path(method.qr).is_file():
            photo = FSInputFile(method.qr)
        else:
            photo = method.qr
    elif method.url:
        photo = generate_payment_qr(method)

    if photo is not None:
        try:
            await callback.message.answer_photo(
                photo,
                caption=caption,
                reply_markup=payment_action_keyboard(
                    method, language, data, amount_minor
                ),
            )
        except Exception:
            logging.exception("Не удалось отправить QR для %s", method.name)
            await callback.message.answer(
                caption,
                reply_markup=payment_action_keyboard(
                    method, language, data, amount_minor
                ),
            )
    else:
        await callback.message.answer(
            caption,
            reply_markup=payment_action_keyboard(method, language, data, amount_minor),
        )
    await callback.message.answer(
        translate(
            language,
            "После оплаты отправьте фото или файл чека.",
            "After payment, send a photo or file of the receipt.",
            "Төлөгөндөн кийин чектин сүрөтүн же файлын жөнөтүңүз.",
        ),
        reply_markup=cancel_keyboard(language),
    )


@router.callback_query(F.data.startswith("bank:missing:"))
async def missing_bank_link(callback: CallbackQuery) -> None:
    await callback.answer(
        localize(
            callback.from_user.id,
            "Ссылка этого банка ещё не настроена администратором.",
            "This bank link has not been configured yet.",
        ),
        show_alert=True,
    )


async def finish_deposit(
    message: Message,
    state: FSMContext,
    bot: Bot,
    proof_file_id: str,
    proof_type: str,
) -> None:
    if message.from_user is None:
        return
    cancel_payment_timer(message.from_user.id)
    data = await state.get_data()
    operation = database.create_deposit(
        user_id=message.from_user.id,
        amount_minor=int(data["amount_minor"]),
        payment_total_minor=int(
            data.get("payment_total_minor", data["amount_minor"])
        ),
        card_id=(int(data["card_id"]) if data.get("card_id") else None),
        platform=str(data["platform"]),
        platform_account_id=str(data["account_id"]),
        payment_method=str(data["payment_method"]),
        proof_file_id=proof_file_id,
        proof_type=proof_type,
    )
    database.finalize_payment_reservation(message.from_user.id)
    await state.clear()
    await notify_admins(bot, operation, message.from_user)
    language = get_language(message.from_user.id)
    await message.answer(
        localize(
            message.from_user.id,
            f"Заявка на пополнение <b>#{operation.id}</b> создана.\n"
            f"Сумма: <b>{format_money(operation.amount_minor)}</b>\n"
            "Баланс игровой платформы будет пополнен после проверки чека.",
            f"Deposit request <b>#{operation.id}</b> has been created.\n"
            f"Amount: <b>{format_money(operation.amount_minor)}</b>\n"
            "Your gaming platform balance will be credited after receipt verification.",
        ),
        reply_markup=main_keyboard(language),
    )


@router.message(DepositFlow.proof, F.photo)
async def deposit_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.photo:
        await finish_deposit(
            message, state, bot, message.photo[-1].file_id, "photo"
        )


@router.message(DepositFlow.proof, F.document)
async def deposit_document(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.document:
        await finish_deposit(
            message, state, bot, message.document.file_id, "document"
        )


@router.message(DepositFlow.proof)
async def deposit_proof_invalid(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        localize(
            message.from_user.id,
            "Нужно отправить фото или файл чека.",
            "Please send a receipt photo or file.",
        )
    )


@router.message(WithdrawalFlow.payout, F.text)
async def withdrawal_payout_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    details = (message.text or "").strip()
    if len(details) < 4 or len(details) > 500:
        await message.answer(
            localize(
                message.from_user.id,
                "Реквизиты должны содержать от 4 до 500 символов.",
                "Payout details must contain 4–500 characters.",
            )
        )
        return
    await state.update_data(payout_details=details)
    await ask_withdrawal_evidence(message, state, message.from_user.id)


@router.message(WithdrawalFlow.payout, F.photo)
async def withdrawal_payout_photo(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.photo:
        return
    await state.update_data(
        payout_file_id=message.photo[-1].file_id,
        payout_file_type="photo",
    )
    await ask_withdrawal_evidence(message, state, message.from_user.id)


@router.message(WithdrawalFlow.payout, F.document)
async def withdrawal_payout_document(
    message: Message, state: FSMContext
) -> None:
    if message.from_user is None or message.document is None:
        return
    await state.update_data(
        payout_file_id=message.document.file_id,
        payout_file_type="document",
    )
    await ask_withdrawal_evidence(message, state, message.from_user.id)


@router.message(WithdrawalFlow.payout)
async def withdrawal_payout_invalid(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        localize(
            message.from_user.id,
            "Отправьте реквизиты текстом или изображение QR-кода.",
            "Send payout details as text or a QR-code image.",
        )
    )


@router.message(WithdrawalFlow.evidence, F.text)
async def withdrawal_evidence_text(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    code = (message.text or "").strip()
    if not 2 <= len(code) <= 500:
        await message.answer("Некорректный код / Invalid code")
        return
    await finish_withdrawal(message, state, bot, code, None, None)


@router.message(WithdrawalFlow.evidence, F.photo)
async def withdrawal_evidence_photo(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if message.photo:
        await finish_withdrawal(
            message, state, bot, None, message.photo[-1].file_id, "photo"
        )


@router.message(WithdrawalFlow.evidence, F.document)
async def withdrawal_evidence_document(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if message.document:
        await finish_withdrawal(
            message, state, bot, None, message.document.file_id, "document"
        )


@router.message(WithdrawalFlow.evidence)
async def withdrawal_evidence_invalid(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        localize(
            message.from_user.id,
            "Отправьте код текстом или скриншот заявки.",
            "Send the code as text or a request screenshot.",
        )
    )


@router.message(Command("profile"))
@router.message(
    F.text.in_(
        {
            MAIN_LABELS["profile"]["ru"],
            MAIN_LABELS["profile"]["en"],
            MAIN_LABELS["profile"]["ky"],
            YELLOW_MAIN_LABELS["profile"]["ru"],
            YELLOW_MAIN_LABELS["profile"]["en"],
            YELLOW_MAIN_LABELS["profile"]["ky"],
            LEGACY_MAIN_LABELS["profile"]["ru"],
            LEGACY_MAIN_LABELS["profile"]["en"],
            LEGACY_MAIN_LABELS["profile"]["ky"],
        }
    )
)
async def profile_handler(message: Message, bot: Bot) -> None:
    profile = await ensure_ready(message, bot)
    if profile is None or message.from_user is None:
        return
    language = profile.language or "ru"
    accounts = database.list_platform_accounts(profile.telegram_id)
    stats = database.get_statistics(profile.telegram_id)
    accounts_text = (
        "\n".join(
            f"• {html.escape(platform)}: <code>{html.escape(account_id)}</code>"
            for platform, account_id in accounts
        )
        or translate(
            language,
            "• Пока не сохранены",
            "• Not saved",
            "• Азырынча сакталган эмес",
        )
    )
    referral_link = (
        f"https://t.me/{bot_username}?start={profile.telegram_id}"
        if bot_username
        else "—"
    )
    if language == "en":
        text = (
            "<b>👤 PLAYER ACCOUNT</b>\n\n"
            f"🆔 Telegram ID: <code>{profile.telegram_id}</code>\n"
            "🌐 System language: EN\n"
            f"📜 Agreement accepted: {html.escape(profile.accepted_terms_at or '—')}\n\n"
            "<b>🎮 Saved platform IDs:</b>\n"
            f"{accounts_text}\n\n"
            "<b>📊 Operation statistics:</b>\n"
            f"📥 Deposits today: {format_money(stats.deposits_today_minor)}\n"
            f"📥 Deposits this month: {format_money(stats.deposits_month_minor)}\n"
            f"📤 Withdrawals today: {format_money(stats.withdrawals_today_minor)}\n"
            f"📤 Withdrawals this month: {format_money(stats.withdrawals_month_minor)}\n\n"
            "<b>👥 Referral program:</b>\n"
            f"🔗 Your link: {html.escape(referral_link)}\n"
            f"💰 Referral balance: {format_money(profile.referral_balance_minor)}\n"
            f"<i>Minimum payout: {format_money(settings.min_referral_withdraw_minor)}. "
            "It is credited only to your gaming platform account.</i>"
        )
    elif language == "ky":
        text = (
            "<b>👤 ОЮНЧУНУН ЖЕКЕ КАБИНЕТИ</b>\n\n"
            f"🆔 Telegram ID: <code>{profile.telegram_id}</code>\n"
            "🌐 Системанын тили: KY\n"
            f"📜 Келишим кабыл алынган: {html.escape(profile.accepted_terms_at or '—')}\n\n"
            "<b>🎮 Платформалардагы сакталган ID:</b>\n"
            f"{accounts_text}\n\n"
            "<b>📊 Операциялардын статистикасы:</b>\n"
            f"📥 Бүгүнкү толуктоолор: {format_money(stats.deposits_today_minor)}\n"
            f"📥 Бул айдагы толуктоолор: {format_money(stats.deposits_month_minor)}\n"
            f"📤 Бүгүнкү чыгаруулар: {format_money(stats.withdrawals_today_minor)}\n"
            f"📤 Бул айдагы чыгаруулар: {format_money(stats.withdrawals_month_minor)}\n\n"
            "<b>👥 Рефералдык программа:</b>\n"
            f"🔗 Сиздин шилтеме: {html.escape(referral_link)}\n"
            f"💰 Рефералдык баланс: {format_money(profile.referral_balance_minor)}\n"
            f"<i>Минималдуу чыгаруу: {format_money(settings.min_referral_withdraw_minor)}. "
            "Каражат оюн платформаңыздагы аккаунтка гана түшөт.</i>"
        )
    else:
        text = (
            "<b>👤 ЛИЧНЫЙ КАБИНЕТ ИГРОКА</b>\n\n"
            f"🆔 Ваш Telegram ID: <code>{profile.telegram_id}</code>\n"
            "🌐 Язык системы: RU\n"
            f"📜 Оферта подписана: {html.escape(profile.accepted_terms_at or '—')}\n\n"
            "<b>🎮 Сохранённые ID на платформах:</b>\n"
            f"{accounts_text}\n\n"
            "<b>📊 Статистика операций:</b>\n"
            f"📥 Пополнения за сегодня: {format_money(stats.deposits_today_minor)}\n"
            f"📥 Пополнения за месяц: {format_money(stats.deposits_month_minor)}\n"
            f"📤 Выводы за сегодня: {format_money(stats.withdrawals_today_minor)}\n"
            f"📤 Выводы за месяц: {format_money(stats.withdrawals_month_minor)}\n\n"
            "<b>👥 Реферальная программа:</b>\n"
            f"🔗 Ваша ссылка: {html.escape(referral_link)}\n"
            f"💰 Накопленный реф. баланс: "
            f"{format_money(profile.referral_balance_minor)}\n"
            f"<i>Минимум для вывода: "
            f"{format_money(settings.min_referral_withdraw_minor)}. "
            "Зачисление производится только на ваш игровой аккаунт.</i>"
        )
    await message.answer(text, reply_markup=referral_keyboard(language))


@router.callback_query(F.data == "referral:withdraw")
async def referral_withdrawal(
    callback: CallbackQuery, state: FSMContext
) -> None:
    profile = get_profile(callback.from_user.id)
    if (
        profile is None
        or profile.referral_balance_minor < settings.min_referral_withdraw_minor
    ):
        await callback.answer(
            localize(
                callback.from_user.id,
                f"Минимум: {format_money(settings.min_referral_withdraw_minor)}",
                f"Minimum: {format_money(settings.min_referral_withdraw_minor)}",
            ),
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message is not None:
        await begin_platform_flow(
            callback.message,
            state,
            "referral",
            user_id=callback.from_user.id,
        )


@router.message(Command("terms"))
@router.message(
    F.text.in_(
        {
            MAIN_LABELS["terms"]["ru"],
            MAIN_LABELS["terms"]["en"],
            MAIN_LABELS["terms"]["ky"],
            YELLOW_MAIN_LABELS["terms"]["ru"],
            YELLOW_MAIN_LABELS["terms"]["en"],
            YELLOW_MAIN_LABELS["terms"]["ky"],
            LEGACY_MAIN_LABELS["terms"]["ru"],
            LEGACY_MAIN_LABELS["terms"]["en"],
            LEGACY_MAIN_LABELS["terms"]["ky"],
        }
    )
)
async def terms_handler(message: Message) -> None:
    if message.from_user is None:
        return
    register_user(message.from_user)
    language = get_language(message.from_user.id)
    await send_terms(message, language, False)


@router.message(Command("requests"))
async def user_operations(message: Message, bot: Bot) -> None:
    profile = await ensure_ready(message, bot)
    if profile is None:
        return
    language = profile.language or "ru"
    operations = database.list_user_operations(profile.telegram_id)
    if not operations:
        await message.answer(
            translate(
                language,
                "У вас пока нет заявок.",
                "You have no requests yet.",
                "Азырынча өтүнмөлөрүңүз жок.",
            )
        )
        return
    title = translate(
        language,
        "<b>Последние заявки:</b>",
        "<b>Recent requests:</b>",
        "<b>Акыркы өтүнмөлөр:</b>",
    )
    lines = [title]
    for operation in operations:
        platform = f", {html.escape(operation.platform)}" if operation.platform else ""
        lines.append(
            f"#{operation.id} — {operation_kind(operation, language)}{platform}, "
            f"{format_money(operation.amount_minor)}, "
            f"{operation_status(operation.status, language)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    await show_admin_home(message)


@router.callback_query(F.data.startswith("operation:"))
async def process_operation(callback: CallbackQuery, bot: Bot) -> None:
    if database.get_staff_role(callback.from_user.id) is None:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "reject"}:
        await callback.answer("Неверная команда.", show_alert=True)
        return
    try:
        operation_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный номер заявки.", show_alert=True)
        return

    decision = "approved" if parts[1] == "approve" else "rejected"
    success, result_text, operation, referrer_id, referral_bonus = (
        database.process_operation(
            operation_id,
            decision,
            callback.from_user.id,
            settings.referral_percent_basis_points,
        )
    )
    if not success or operation is None:
        await callback.answer(result_text, show_alert=True)
        return

    await audit_event(
        bot,
        callback.from_user,
        (
            f"Заявка #{operation.id} подтверждена"
            if decision == "approved"
            else f"Заявка #{operation.id} отклонена"
        ),
        title="💱 ОБРАБОТКА ТРАНЗАКЦИИ",
        entity_type="operation",
        entity_id=str(operation.id),
        details=(
            f"👤 Игрок: <code>{operation.user_id}</code>\n"
            f"💰 Сумма: <b>{format_money(operation.amount_minor)}</b>"
        ),
    )

    language = get_language(operation.user_id)
    if decision == "approved" and operation.kind == "deposit":
        player_notification = translate(
            language,
            f"✅ <b>Платёж подтверждён!</b>\n\n💵 Сумма: <b>{format_money(operation.amount_minor)}</b>\n🎮 {html.escape(operation.platform or 'Игровой счёт')}: <code>{html.escape(operation.platform_account_id or '—')}</code>\n🧾 Чек проверен администратором.",
            f"✅ <b>Payment confirmed!</b>\n\n💵 Amount: <b>{format_money(operation.amount_minor)}</b>\n🎮 {html.escape(operation.platform or 'Gaming account')}: <code>{html.escape(operation.platform_account_id or '—')}</code>\n🧾 The receipt was verified by an administrator.",
            f"✅ <b>Төлөм ырасталды!</b>\n\n💵 Сумма: <b>{format_money(operation.amount_minor)}</b>\n🎮 {html.escape(operation.platform or 'Оюн эсеби')}: <code>{html.escape(operation.platform_account_id or '—')}</code>\n🧾 Чек администратор тарабынан текшерилди.",
        )
    else:
        player_notification = translate(
            language,
            f"Заявка <b>#{operation.id}</b> ({operation_kind(operation, language).lower()}, {format_money(operation.amount_minor)}) {operation_status(operation.status, language)}.",
            f"Request <b>#{operation.id}</b> ({operation_kind(operation, language).lower()}, {format_money(operation.amount_minor)}) {operation_status(operation.status, language)}.",
            f"Өтүнмө <b>#{operation.id}</b> ({operation_kind(operation, language).lower()}, {format_money(operation.amount_minor)}) {operation_status(operation.status, language)}.",
        )
    try:
        await bot.send_message(
            operation.user_id,
            player_notification,
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s", operation.user_id)

    if referrer_id and referral_bonus > 0:
        try:
            await bot.send_message(
                referrer_id,
                localize(
                    referrer_id,
                    f"🎁 Начислен реферальный бонус: "
                    f"<b>{format_money(referral_bonus)}</b>",
                    f"🎁 Referral bonus credited: "
                    f"<b>{format_money(referral_bonus)}</b>",
                ),
            )
        except Exception:
            logging.exception("Не удалось уведомить реферера %s", referrer_id)

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(
            f"Заявка #{operation.id}: {operation_status(operation.status)}.\n"
            f"Обработал администратор <code>{callback.from_user.id}</code>."
        )
    await callback.answer("Готово")


@router.message(StateFilter(None))
async def unknown(message: Message) -> None:
    if message.from_user is None:
        return
    profile = get_profile(message.from_user.id)
    if profile is None:
        return
    if profile.language not in SUPPORTED_LANGUAGES:
        database.set_language(message.from_user.id, "ru")
        profile = get_profile(message.from_user.id)
        if profile is None:
            return
    await message.answer(
        translate(
            profile.language,
            "Выберите действие в меню или отправьте /start.",
            "Select an action from the menu or send /start.",
            "Менюдан аракетти тандаңыз же /start жөнөтүңүз.",
        ),
        reply_markup=main_keyboard(profile.language),
    )


async def configure_bot_profile(bot: Bot) -> None:
    commands_ru = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="deposit", description="Пополнить игровой счёт"),
        BotCommand(command="withdraw", description="Вывести средства"),
        BotCommand(command="profile", description="Личный кабинет"),
        BotCommand(command="requests", description="Мои заявки"),
        BotCommand(command="terms", description="Соглашение и правила"),
        BotCommand(command="language", description="Изменить язык"),
        BotCommand(command="cancel", description="Отменить действие"),
        BotCommand(command="admin", description="Панель управления"),
        BotCommand(command="chatid", description="ID текущего чата"),
    ]
    commands_en = [
        BotCommand(command="start", description="Main menu"),
        BotCommand(command="deposit", description="Deposit to gaming account"),
        BotCommand(command="withdraw", description="Withdraw funds"),
        BotCommand(command="profile", description="Player account"),
        BotCommand(command="requests", description="My requests"),
        BotCommand(command="terms", description="Terms and rules"),
        BotCommand(command="language", description="Change language"),
        BotCommand(command="cancel", description="Cancel current action"),
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="chatid", description="Current chat ID"),
    ]
    commands_ky = [
        BotCommand(command="start", description="Башкы меню"),
        BotCommand(command="deposit", description="Оюн эсебин толуктоо"),
        BotCommand(command="withdraw", description="Каражат чыгаруу"),
        BotCommand(command="profile", description="Жеке кабинет"),
        BotCommand(command="requests", description="Менин өтүнмөлөрүм"),
        BotCommand(command="terms", description="Келишим жана эрежелер"),
        BotCommand(command="language", description="Тилди өзгөртүү"),
        BotCommand(command="cancel", description="Аракетти жокко чыгаруу"),
        BotCommand(command="admin", description="Башкаруу панели"),
        BotCommand(command="chatid", description="Учурдагы чаттын ID-си"),
    ]
    await bot.set_my_commands(commands_ru)
    await bot.set_my_commands(commands_ru, language_code="ru")
    await bot.set_my_commands(commands_en, language_code="en")
    await bot.set_my_commands(commands_ky, language_code="ky")
    try:
        await bot.set_my_name(name=settings.service_name)
        await bot.set_my_name(name=settings.service_name, language_code="ru")
        await bot.set_my_name(name=settings.service_name, language_code="en")
        await bot.set_my_name(name=settings.service_name, language_code="ky")
        await bot.set_my_short_description(
            short_description=(
                "Пополнение и вывод средств для игровых платформ."
            ),
            language_code="ru",
        )
        await bot.set_my_short_description(
            short_description=(
                "Deposits and withdrawals for gaming platforms."
            ),
            language_code="en",
        )
        await bot.set_my_description(
            description=(
                "💎 MOMENTUM SERVICE\n"
                "⚡ Быстрое создание заявок на пополнение.\n"
                "📤 Удобный вывод средств.\n"
                "🔒 Проверка операций администратором.\n\n"
                "Перед использованием ознакомьтесь с соглашением и правилами."
            ),
            language_code="ru",
        )
        await bot.set_my_description(
            description=(
                "💎 MOMENTUM SERVICE\n"
                "⚡ Fast deposit requests.\n"
                "📤 Convenient withdrawals.\n"
                "🔒 Administrator verification.\n\n"
                "Read and accept the terms before using the service."
            ),
            language_code="en",
        )
    except Exception:
        logging.exception("Не удалось полностью обновить профиль BotFather")


async def main() -> None:
    global settings, database, bot_username
    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    database.sync_admin_configuration(
        settings.admin_ids,
        settings.platforms,
        tuple((method.key, method.name) for method in settings.payment_methods),
    )
    configure_admin_panel(database, settings)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(MaintenanceMiddleware())
    dispatcher.callback_query.outer_middleware(MaintenanceMiddleware())
    dispatcher.include_router(admin_router)
    dispatcher.include_router(router)

    me = await bot.get_me()
    bot_username = me.username or ""
    await configure_bot_profile(bot)
    logging.info("Бот @%s запущен", bot_username)
    web_runner = await start_smart_link_server()
    try:
        await dispatcher.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        logging.error("%s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
