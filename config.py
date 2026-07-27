from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_admin_ids(raw: str) -> frozenset[int]:
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError as exc:
            raise RuntimeError(
                f"ADMIN_IDS содержит неверное значение: {item!r}"
            ) from exc
    return frozenset(result)


def _money_to_minor(raw: str, name: str) -> int:
    try:
        value = Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"))
        if not value.is_finite() or value <= 0:
            raise ValueError
        return int(value * 100)
    except (InvalidOperation, AttributeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"{name} должен быть положительным числом") from exc


def _percent_to_basis_points(raw: str) -> int:
    try:
        value = Decimal(raw.replace(",", "."))
        if not value.is_finite() or value < 0 or value > 100:
            raise ValueError
        return int((value * 100).quantize(Decimal("1")))
    except (InvalidOperation, AttributeError, ValueError, OverflowError) as exc:
        raise RuntimeError("REFERRAL_PERCENT должен быть числом от 0 до 100") from exc


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return key or "METHOD"


BANK_LABELS = {
    "MBANK": "MBANK",
    "OPTIMA": "Optima",
    "OMONEY": "O!Деньги",
    "BAKAI_BANK": "BAKAI BANK",
    "SIMBANK": "Simbank",
}

BANK_APP_LINKS = {
    "MBANK": {
        "android": "https://play.google.com/store/apps/details?id=com.maanavan.mb_kyrgyzstan",
        "ios": "https://apps.apple.com/kg/app/id922922121",
    },
    "OMONEY": {
        "android": "https://play.google.com/store/apps/details?id=kg.o.nurtelecom",
        "ios": "https://apps.apple.com/kg/app/id1257075492",
    },
    "MEGAPAY": {
        "android": "https://play.google.com/store/apps/details?id=com.kp.megapay.kg",
        "ios": "https://apps.apple.com/kg/app/id1317275502",
    },
    "BALANCE": {
        "android": "https://play.google.com/store/apps/details?id=ru.mitapp.beeline_wallet",
        "ios": "https://apps.apple.com/kg/app/id1266413018",
    },
    "BAKAI_BANK": {
        "android": "https://play.google.com/store/apps/details?id=kg.bta.mobilebank2",
        "ios": "https://apps.apple.com/kg/app/id1294108123",
    },
}


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    key: str
    name: str
    details: str
    qr: str | None
    url: str | None
    android_url: str | None
    ios_url: str | None


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    required_channel: str | None
    required_channel_url: str | None
    service_name: str
    support_username: str
    currency: str
    platforms: tuple[str, ...]
    platform_icon_custom_emoji_ids: dict[str, str]
    payment_methods: tuple[PaymentMethod, ...]
    payment_qr_image: str | None
    payment_qr_url_template: str | None
    smart_link_base_url: str | None
    min_deposit_minor: int
    max_deposit_minor: int
    deposit_fee_basis_points: int
    payment_timeout_seconds: int
    min_withdraw_minor: int
    max_withdraw_minor: int
    referral_percent_basis_points: int
    min_referral_withdraw_minor: int
    deposit_instructions: str
    database_path: Path


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Откройте файл .env и вставьте НОВЫЙ токен BotFather."
        )
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        raise RuntimeError("BOT_TOKEN выглядит неверно. Проверьте значение в файле .env.")

    currency = os.getenv("CURRENCY", "KGS").strip().upper() or "KGS"
    platforms = _csv(
        os.getenv(
            "PLATFORMS",
            "1XBET,MELBET,1WIN,MOSTBET,WINWIN,888STARZ",
        )
    )
    if not platforms:
        raise RuntimeError("PLATFORMS не должен быть пустым")
    platform_icon_custom_emoji_ids: dict[str, str] = {}
    for platform in platforms:
        env_name = f"PLATFORM_{_env_key(platform)}_ICON_CUSTOM_EMOJI_ID"
        custom_emoji_id = os.getenv(env_name, "").strip()
        if custom_emoji_id and not custom_emoji_id.isdigit():
            raise RuntimeError(f"{env_name} должен содержать числовой ID")
        if custom_emoji_id:
            platform_icon_custom_emoji_ids[platform] = custom_emoji_id

    default_instructions = os.getenv(
        "DEPOSIT_INSTRUCTIONS",
        "После перевода отправьте боту фото или файл чека.",
    ).strip()
    methods: list[PaymentMethod] = []
    for configured_name in _csv(
        os.getenv(
            "PAYMENT_METHODS",
            "MBANK,OPTIMA,BAKAI_BANK,OMONEY,SIMBANK",
        )
    ):
        key = _env_key(configured_name)
        name = BANK_LABELS.get(key, configured_name)
        details = os.getenv(
            f"PAYMENT_{key}_DETAILS",
            default_instructions,
        ).strip()
        qr = os.getenv(f"PAYMENT_{key}_QR", "").strip() or None
        url = os.getenv(f"PAYMENT_{key}_URL", "").strip() or None
        default_links = BANK_APP_LINKS.get(key, {})
        android_url = (
            os.getenv(f"PAYMENT_{key}_ANDROID_URL", "").strip()
            or default_links.get("android")
        )
        ios_url = (
            os.getenv(f"PAYMENT_{key}_IOS_URL", "").strip()
            or default_links.get("ios")
        )
        for variable, configured_url in (
            (f"PAYMENT_{key}_URL", url),
            (f"PAYMENT_{key}_ANDROID_URL", android_url),
            (f"PAYMENT_{key}_IOS_URL", ios_url),
        ):
            if configured_url and not re.match(
                r"^https://", configured_url, flags=re.IGNORECASE
            ):
                raise RuntimeError(f"{variable} должен начинаться с https://")
        if qr and not re.match(r"^(https?://|[A-Za-z0-9_-]{20,}$)", qr):
            qr_path = Path(qr)
            if not qr_path.is_absolute():
                qr_path = BASE_DIR / qr_path
            qr = str(qr_path)
        methods.append(
            PaymentMethod(
                key=key,
                name=name,
                details=details,
                qr=qr,
                url=url,
                android_url=android_url,
                ios_url=ios_url,
            )
        )
    if not methods:
        raise RuntimeError("PAYMENT_METHODS не должен быть пустым")

    payment_qr_image = os.getenv("PAYMENT_QR_IMAGE", "").strip() or None
    if payment_qr_image and not re.match(
        r"^(https?://|[A-Za-z0-9_-]{20,}$)", payment_qr_image
    ):
        qr_path = Path(payment_qr_image)
        if not qr_path.is_absolute():
            qr_path = BASE_DIR / qr_path
        payment_qr_image = str(qr_path)

    payment_qr_url_template = (
        os.getenv("PAYMENT_QR_URL_TEMPLATE", "").strip() or None
    )
    if payment_qr_url_template and not re.match(
        r"^https://", payment_qr_url_template, flags=re.IGNORECASE
    ):
        raise RuntimeError("PAYMENT_QR_URL_TEMPLATE должен начинаться с https://")

    smart_link_base_url = os.getenv("SMART_LINK_BASE_URL", "").strip().rstrip("/")
    if not smart_link_base_url:
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            smart_link_base_url = f"https://{railway_domain}"
    if smart_link_base_url and not re.match(
        r"^https://", smart_link_base_url, flags=re.IGNORECASE
    ):
        raise RuntimeError("SMART_LINK_BASE_URL должен начинаться с https://")

    raw_db_path = os.getenv("DATABASE_PATH", "").strip()
    if not raw_db_path:
        railway_volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        if railway_volume_path:
            raw_db_path = str(Path(railway_volume_path) / "bot.sqlite3")
        else:
            raw_db_path = "data/bot.sqlite3"
    database_path = Path(raw_db_path)
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path

    min_deposit = _money_to_minor(
        os.getenv("MIN_DEPOSIT", "35"), "MIN_DEPOSIT"
    )
    max_deposit = _money_to_minor(
        os.getenv("MAX_DEPOSIT", "500000"), "MAX_DEPOSIT"
    )
    min_withdraw = _money_to_minor(
        os.getenv("MIN_WITHDRAW", "500"), "MIN_WITHDRAW"
    )
    max_withdraw = _money_to_minor(
        os.getenv("MAX_WITHDRAW", "500000"), "MAX_WITHDRAW"
    )
    if min_deposit > max_deposit:
        raise RuntimeError("MIN_DEPOSIT не может быть больше MAX_DEPOSIT")
    if min_withdraw > max_withdraw:
        raise RuntimeError("MIN_WITHDRAW не может быть больше MAX_WITHDRAW")
    try:
        payment_timeout_seconds = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", "300"))
    except ValueError as exc:
        raise RuntimeError("PAYMENT_TIMEOUT_SECONDS должен быть целым числом") from exc
    if not 30 <= payment_timeout_seconds <= 3600:
        raise RuntimeError(
            "PAYMENT_TIMEOUT_SECONDS должен быть от 30 до 3600 секунд"
        )

    support = os.getenv("SUPPORT_USERNAME", "").strip()
    if support and not support.startswith("@"):
        support = f"@{support}"
    required_channel = os.getenv("REQUIRED_CHANNEL", "").strip() or None
    required_channel_url = os.getenv("REQUIRED_CHANNEL_URL", "").strip() or None
    if required_channel_url and not re.match(r"^https://", required_channel_url, flags=re.IGNORECASE):
        raise RuntimeError("REQUIRED_CHANNEL_URL must start with https://")

    return Settings(
        bot_token=token,
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        required_channel=required_channel,
        required_channel_url=required_channel_url,
        service_name=os.getenv("SERVICE_NAME", "MOBCASH").strip()
        or "MOBCASH",
        support_username=support,
        currency=currency,
        platforms=platforms,
        platform_icon_custom_emoji_ids=platform_icon_custom_emoji_ids,
        payment_methods=tuple(methods),
        payment_qr_image=payment_qr_image,
        payment_qr_url_template=payment_qr_url_template,
        smart_link_base_url=smart_link_base_url or None,
        min_deposit_minor=min_deposit,
        max_deposit_minor=max_deposit,
        deposit_fee_basis_points=_percent_to_basis_points(
            os.getenv("DEPOSIT_FEE_PERCENT", "0")
        ),
        payment_timeout_seconds=payment_timeout_seconds,
        min_withdraw_minor=min_withdraw,
        max_withdraw_minor=max_withdraw,
        referral_percent_basis_points=_percent_to_basis_points(
            os.getenv("REFERRAL_PERCENT", "1")
        ),
        min_referral_withdraw_minor=_money_to_minor(
            os.getenv("MIN_REFERRAL_WITHDRAW", "500"),
            "MIN_REFERRAL_WITHDRAW",
        ),
        deposit_instructions=default_instructions,
        database_path=database_path,
    )
