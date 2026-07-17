"""
═══════════════════════════════════════════════════════════════
 🪑 GOLD MEBEL BOT — Mebel ustaxonasi uchun Telegram bot
═══════════════════════════════════════════════════════════════
Bitta faylga birlashtirilgan (yagona main.py) versiya.
Kutubxona: aiogram 3.x | Ma'lumotlar bazasi: SQLite (aiosqlite)
Arxitektura: Asinxron, FSM (Finite State Machine) asosida.

Ishga tushirish:
    1) pip install -r requirements.txt
    2) .env faylini to'ldiring (BOT_TOKEN, ADMIN_IDS)
    3) python main.py
═══════════════════════════════════════════════════════════════
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gold_mebel_bot")


# ══════════════════════════════════════════════════════════════
# ⚙️ 1. KONFIGURATSIYA
# ══════════════════════════════════════════════════════════════
load_dotenv()  # .env faylidagi o'zgaruvchilarni yuklaydi

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError(
        "❌ BOT_TOKEN topilmadi! .env faylini yarating va BOT_TOKEN=... qiymatini kiriting "
        "(namuna uchun .env.example fayliga qarang)."
    )

_raw_admins = os.getenv("ADMIN_IDS", "")
INITIAL_ADMIN_IDS: list[int] = [
    int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit()
]

DB_PATH: str = os.getenv("DB_PATH", "gold_mebel.db")
SHOP_NAME: str = os.getenv("SHOP_NAME", "Gold Mebel")
CATALOG_COLUMNS: int = int(os.getenv("CATALOG_COLUMNS", "2"))


# ══════════════════════════════════════════════════════════════
# 🗄 2. MA'LUMOTLAR BAZASI (SQLite)
# ══════════════════════════════════════════════════════════════
@asynccontextmanager
async def get_db():
    """
    Bazaga xavfsiz ulanish uchun context manager.
    Foreign keys yoqilgan holda ulanadi (categories o'chganda products ham
    kaskad tarzda o'chishi uchun) va Row factory orqali natijalarni
    lug'at (dict) kabi ishlatish imkonini beradi.
    """
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        logger.exception("Baza operatsiyasida xatolik yuz berdi, o'zgarishlar bekor qilindi.")
        raise
    finally:
        await conn.close()


async def init_db() -> None:
    """Bot birinchi marta ishga tushganda barcha jadvallarni yaratadi."""
    async with get_db() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       INTEGER UNIQUE NOT NULL,
                full_name   TEXT NOT NULL,
                phone       TEXT,
                is_blocked  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS staff (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       INTEGER UNIQUE NOT NULL,
                full_name   TEXT,
                role        TEXT NOT NULL CHECK (role IN ('admin', 'usta')),
                added_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                price       TEXT NOT NULL,
                photo_id    TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_tg_id  INTEGER NOT NULL,
                customer_name   TEXT NOT NULL,
                phone           TEXT NOT NULL,
                phone2          TEXT,
                product_id      INTEGER,
                product_name    TEXT NOT NULL,
                product_price   TEXT,
                lat             REAL NOT NULL,
                lon             REAL NOT NULL,
                status          TEXT NOT NULL DEFAULT 'new',
                usta_tg_id      INTEGER,
                usta_name       TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );
            """
        )

        # Boshlang'ich sozlamalar (ustaxona ochiq holatda boshlanadi)
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('is_open', '1')"
        )

        # .env faylida ko'rsatilgan super-adminlarni bazaga qo'shamiz
        for admin_id in INITIAL_ADMIN_IDS:
            await db.execute(
                "INSERT OR IGNORE INTO staff(tg_id, full_name, role) VALUES(?, 'Super Admin', 'admin')",
                (admin_id,),
            )

    logger.info("✅ Baza tayyor: %s", DB_PATH)


# ──────────────────────────────────────────
# 🔧 Umumiy yordamchi funksiyalar (query helpers)
# ──────────────────────────────────────────
async def execute(sql: str, params: tuple = ()) -> int:
    """INSERT/UPDATE/DELETE bajaradi, oxirgi qo'shilgan qator ID'sini qaytaradi."""
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        return cursor.lastrowid


async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    """Bitta qatorni dict ko'rinishida qaytaradi (topilmasa None)."""
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    """Barcha mos qatorlarni dict ro'yxati sifatida qaytaradi."""
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════
# 🧠 3. FSM HOLATLARI
# ══════════════════════════════════════════════════════════════
class Registration(StatesGroup):
    """Yangi mijozni ro'yxatdan o'tkazish."""
    name = State()
    phone = State()


class OrderFlow(StatesGroup):
    """Mijoz buyurtma berish jarayoni."""
    phone = State()
    phone2 = State()
    location = State()
    confirm = State()


class CategoryFSM(StatesGroup):
    """Admin: bo'lim qo'shish / tahrirlash."""
    add_name = State()
    edit_name = State()


class ProductFSM(StatesGroup):
    """Admin: mahsulot qo'shish / tahrirlash."""
    choose_category = State()
    name = State()
    description = State()
    price = State()
    photo = State()
    edit_field_value = State()


class StaffFSM(StatesGroup):
    """Admin: xodim (Usta/Admin) qo'shish."""
    enter_id = State()
    choose_role = State()


class BroadcastFSM(StatesGroup):
    """Admin: barcha mijozlarga xabar yuborish."""
    content = State()

# ══════════════════════════════════════════════════════════════
# ⌨️ 4. KLAVIATURALAR
# ══════════════════════════════════════════════════════════════
# ──────────────────────────────────────────
# 🧱 Universal quruvchilar
# ──────────────────────────────────────────
def ik(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Inline klaviatura quruvchi: ik([("Matn", "callback")], ...)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
            for row in rows
        ]
    )


def rk(*rows, resize: bool = True) -> ReplyKeyboardMarkup:
    """Pastki (reply) klaviatura quruvchi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t) if isinstance(t, str) else t for t in row]
            for row in rows
        ],
        resize_keyboard=resize,
    )


# Har bir bosqichda foydalanuvchi jarayonni bekor qila olishi shart bo'lgan tugma
CANCEL_TEXT = "❌ Bekor qilish"
CANCEL_KB = rk([CANCEL_TEXT])

SKIP_TEXT = "➡️ O'tkazib yuborish"
SKIP_OR_CANCEL_KB = rk([SKIP_TEXT], [CANCEL_TEXT])


# ──────────────────────────────────────────
# 🏠 Asosiy menyular (rolga qarab)
# ──────────────────────────────────────────
def customer_main_kb() -> ReplyKeyboardMarkup:
    return rk(
        ["🛍 Katalog"],
        ["📦 Buyurtmalarim", "👤 Profilim"],
        ["🆘 Yordam"],
    )


def admin_main_kb() -> ReplyKeyboardMarkup:
    return rk(
        ["📂 Bo'limlar", "📦 Mahsulotlar"],
        ["👥 Xodimlar", "⚙️ Ustaxona holati"],
        ["📨 Xabar yuborish", "🛍 Katalog"],
        ["🔙 Mijoz menyusi"],
    )


def usta_main_kb() -> ReplyKeyboardMarkup:
    return rk(
        ["📋 Yangi buyurtmalar", "✅ Mening buyurtmalarim"],
        ["🔙 Mijoz menyusi"],
    )


def role_switch_row(is_admin: bool, is_usta: bool) -> list[str]:
    """Admin/Usta panelga o'tish tugmalari (main menyuga qo'shiladi)."""
    row = []
    if is_admin:
        row.append("⚙️ Admin Panel")
    if is_usta:
        row.append("🔨 Usta Panel")
    return row


async def main_kb_for(tg_id: int) -> ReplyKeyboardMarkup:
    """Foydalanuvchi roliga qarab bosh menyuni yig'adi."""

    staff = await fetch_one("SELECT role FROM staff WHERE tg_id=?", (tg_id,))
    rows = [["🛍 Katalog"], ["📦 Buyurtmalarim", "👤 Profilim"], ["🆘 Yordam"]]
    extra = role_switch_row(
        is_admin=bool(staff and staff["role"] == "admin"),
        is_usta=bool(staff and staff["role"] == "usta"),
    )
    if extra:
        rows.append(extra)
    return rk(*rows)


# ──────────────────────────────────────────
# 📂 Admin: Bo'limlar
# ──────────────────────────────────────────
def categories_admin_kb(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [[(f"📂 {c['name']}", f"admcat_{c['id']}")] for c in categories]
    rows.append([("➕ Yangi bo'lim qo'shish", "admcat_new")])
    return ik(*rows)


def category_actions_kb(cat_id: int) -> InlineKeyboardMarkup:
    return ik(
        [("✏️ Tahrirlash", f"admcatedit_{cat_id}"), ("🗑 O'chirish", f"admcatdel_{cat_id}")],
        [("🔙 Orqaga", "admcat_back")],
    )


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return ik([("✅ Ha", yes_cb), ("❌ Yo'q", no_cb)])


# ──────────────────────────────────────────
# 📦 Admin: Mahsulotlar
# ──────────────────────────────────────────
def categories_pick_kb(categories: list[dict], prefix: str) -> InlineKeyboardMarkup:
    rows = [[(f"📂 {c['name']}", f"{prefix}_{c['id']}")] for c in categories]
    return ik(*rows) if rows else ik()


def products_admin_kb(products: list[dict], cat_id: int) -> InlineKeyboardMarkup:
    rows = [[(f"📦 {p['name']}", f"admprod_{p['id']}")] for p in products]
    rows.append([("➕ Yangi mahsulot qo'shish", f"admprodnew_{cat_id}")])
    rows.append([("🔙 Bo'limlarga qaytish", "admcat_back")])
    return ik(*rows)


def product_actions_kb(prod_id: int) -> InlineKeyboardMarkup:
    return ik(
        [("✏️ Nomi", f"admprodedit_name_{prod_id}"), ("💬 Tavsif", f"admprodedit_desc_{prod_id}")],
        [("💰 Narxi", f"admprodedit_price_{prod_id}"), ("🖼 Rasmi", f"admprodedit_photo_{prod_id}")],
        [("🗑 O'chirish", f"admproddel_{prod_id}")],
        [("🔙 Orqaga", "admcat_back")],
    )


def skip_photo_kb() -> InlineKeyboardMarkup:
    return ik([("➡️ Rasmsiz qoldirish", "admprod_skipphoto")])


# ──────────────────────────────────────────
# 🛍 Mijoz: Katalog (grid)
# ──────────────────────────────────────────
def catalog_categories_kb(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [[(f"📂 {c['name']}", f"cat_{c['id']}")] for c in categories]
    return ik(*rows) if rows else ik()


def catalog_grid_kb(products: list[dict], cat_id: int) -> InlineKeyboardMarkup:
    """Mahsulotlarni 2 yoki 3 ustunli setka (grid) ko'rinishida chiqaradi."""
    buttons = [
        InlineKeyboardButton(text=f"🪑 {p['name']}", callback_data=f"prod_{p['id']}")
        for p in products
    ]
    rows = [buttons[i:i + CATALOG_COLUMNS] for i in range(0, len(buttons), CATALOG_COLUMNS)]
    keyboard = [[InlineKeyboardButton(text=t.text, callback_data=t.callback_data) for t in row] for row in rows]
    keyboard.append([InlineKeyboardButton(text="🔙 Bo'limlarga qaytish", callback_data="back_to_cats")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def product_view_kb(prod_id: int) -> InlineKeyboardMarkup:
    return ik(
        [("✅ Buyurtma berish", f"order_{prod_id}")],
        [("🔙 Orqaga", "back_to_prods")],
    )


# ──────────────────────────────────────────
# 🛒 Buyurtma jarayoni
# ──────────────────────────────────────────
def phone_request_kb() -> ReplyKeyboardMarkup:
    return rk(
        [KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)],
        [CANCEL_TEXT],
    )


def location_request_kb() -> ReplyKeyboardMarkup:
    return rk(
        [KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)],
        [CANCEL_TEXT],
    )


def order_confirm_kb() -> InlineKeyboardMarkup:
    return ik(
        [("✅ Buyurtmani tasdiqlash", "order_confirm")],
        [("❌ Bekor qilish", "order_cancel_flow")],
    )


def usta_accept_kb(order_id: int) -> InlineKeyboardMarkup:
    return ik([("✅ Qabul qilish", f"accept_{order_id}")])


# ──────────────────────────────────────────
# 👥 Admin: Xodimlar
# ──────────────────────────────────────────
def staff_list_kb(staff: list[dict]) -> InlineKeyboardMarkup:
    icon = {"admin": "🛡", "usta": "🔨"}
    rows = [
        [(f"{icon.get(s['role'], '👤')} {s['full_name'] or s['tg_id']} ({s['role']})", f"staffdel_{s['tg_id']}")]
        for s in staff
    ]
    rows.append([("➕ Yangi xodim qo'shish", "staff_add")])
    return ik(*rows) if rows else ik([("➕ Yangi xodim qo'shish", "staff_add")])


def role_choice_kb() -> InlineKeyboardMarkup:
    return ik(
        [("🛡 Admin", "role_admin"), ("🔨 Usta", "role_usta")],
    )

# ══════════════════════════════════════════════════════════════
# 🤖 BOT VA YAGONA ROUTER
# ══════════════════════════════════════════════════════════════
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
router = Router(name="gold_mebel_bot")


# ══════════════════════════════════════════════════════════════
# 🌐 5. UMUMIY HANDLERLAR (start, ro'yxatdan o'tish, bekor qilish, panel almashtirish)
# ══════════════════════════════════════════════════════════════
async def is_shop_open() -> bool:
    """Ustaxona hozir buyurtma qabul qilyaptimi, tekshiradi."""
    row = await fetch_one("SELECT value FROM settings WHERE key='is_open'")
    return bool(row and row["value"] == "1")


async def get_staff_role(tg_id: int) -> str | None:
    """Foydalanuvchining xodimlik rolini qaytaradi: 'admin', 'usta' yoki None."""
    row = await fetch_one("SELECT role FROM staff WHERE tg_id=?", (tg_id,))
    return row["role"] if row else None


# ──────────────────────────────────────────
# 🌐 Global "Bekor qilish" — har qanday holatda ishlaydi
# ──────────────────────────────────────────
@router.message(F.text == CANCEL_TEXT, StateFilter("*"))
async def global_cancel(msg: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        kb = await main_kb_for(msg.from_user.id)
        await msg.answer("🚫 Amal bekor qilindi.", reply_markup=kb)
    except Exception:
        logger.exception("global_cancel xatosi")


@router.message(F.text == "🔙 Mijoz menyusi", StateFilter("*"))
async def go_home(msg: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        kb = await main_kb_for(msg.from_user.id)
        await msg.answer("🏠 Asosiy menyu", reply_markup=kb)
    except Exception:
        logger.exception("go_home xatosi")


# ──────────────────────────────────────────
# 🚀 /start va ro'yxatdan o'tish
# ──────────────────────────────────────────
@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(msg: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        user = await fetch_one("SELECT * FROM users WHERE tg_id=?", (msg.from_user.id,))

        if user and user["is_blocked"]:
            return await msg.answer("❌ Kechirasiz, sizga botdan foydalanish taqiqlangan.")

        if not user:
            await msg.answer(
                f"👋 <b>{SHOP_NAME}</b> ustaxonasiga xush kelibsiz!\n\n"
                "Ro'yxatdan o'tish uchun ismingizni yuboring:",
                reply_markup=ReplyKeyboardRemove(),
            )
            await state.set_state(Registration.name)
            return

        status = "🟢 Ochiq" if await is_shop_open() else "🔴 Yopiq (hozircha buyurtma qabul qilinmaydi)"
        kb = await main_kb_for(msg.from_user.id)
        await msg.answer(
            f"👋 Salom, <b>{user['full_name']}</b>!\n🏭 Ustaxona holati: {status}",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("cmd_start xatosi")
        await msg.answer("⚠️ Xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")


@router.message(Registration.name)
async def reg_name(msg: Message, state: FSMContext) -> None:
    try:
        if not msg.text or len(msg.text.strip()) < 2:
            return await msg.answer("⚠️ Iltimos, to'g'ri ism kiriting (kamida 2 harf):")
        await state.update_data(full_name=msg.text.strip().title())
        await msg.answer(
            "📱 Endi telefon raqamingizni yuboring (tugma orqali yoki qo'lda yozing):",
            reply_markup=None,
        )
        await msg.answer("👇", reply_markup=phone_request_kb())
        await state.set_state(Registration.phone)
    except Exception:
        logger.exception("reg_name xatosi")


@router.message(Registration.phone)
async def reg_phone(msg: Message, state: FSMContext) -> None:
    try:
        phone = msg.contact.phone_number if msg.contact else (msg.text or "").strip()
        if not phone or len(phone) < 7:
            return await msg.answer("⚠️ Iltimos, to'g'ri telefon raqam kiriting yoki tugmadan foydalaning:")
        if not phone.startswith("+"):
            phone = "+" + phone.lstrip("+")

        data = await state.get_data()
        await execute(
            "INSERT INTO users(tg_id, full_name, phone) VALUES(?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET full_name=excluded.full_name, phone=excluded.phone",
            (msg.from_user.id, data["full_name"], phone),
        )
        await state.clear()
        kb = await main_kb_for(msg.from_user.id)
        await msg.answer(
            f"🎉 Tabriklaymiz, <b>{data['full_name']}</b>! Ro'yxatdan muvaffaqiyatli o'tdingiz.",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("reg_phone xatosi")
        await msg.answer("⚠️ Xatolik yuz berdi, qaytadan urinib ko'ring.")


# ──────────────────────────────────────────
# 👤 Profil va Yordam
# ──────────────────────────────────────────
@router.message(F.text == "👤 Profilim")
async def cmd_profile(msg: Message) -> None:
    try:
        user = await fetch_one("SELECT * FROM users WHERE tg_id=?", (msg.from_user.id,))
        if not user:
            return await msg.answer("Iltimos, avval /start buyrug'ini yuboring.")
        await msg.answer(
            f"👤 <b>Profilingiz</b>\n\n"
            f"📛 Ism: {user['full_name']}\n"
            f"📱 Telefon: {user['phone']}"
        )
    except Exception:
        logger.exception("cmd_profile xatosi")


@router.message(F.text == "🆘 Yordam")
async def cmd_help(msg: Message) -> None:
    await msg.answer(
        f"🆘 <b>Yordam markazi</b>\n\n"
        f"🏭 <b>{SHOP_NAME}</b> ustaxonasi mahsulotlari bilan tanishish va buyurtma berish uchun "
        f"\"🛍 Katalog\" tugmasidan foydalaning.\n\n"
        f"Savollaringiz bo'lsa, ustaxona ma'muriyati bilan bog'laning."
    )


# ──────────────────────────────────────────
# 🔀 Panel almashtirish (Admin Panel / Usta Panel)
# ──────────────────────────────────────────
@router.message(F.text == "⚙️ Admin Panel")
async def open_admin_panel(msg: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        role = await get_staff_role(msg.from_user.id)
        if role != "admin":
            return await msg.answer("⛔ Sizda admin huquqi yo'q.")
        await msg.answer("⚙️ <b>Admin Panel</b>", reply_markup=admin_main_kb())
    except Exception:
        logger.exception("open_admin_panel xatosi")


@router.message(F.text == "🔨 Usta Panel")
async def open_usta_panel(msg: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        role = await get_staff_role(msg.from_user.id)
        if role != "usta":
            return await msg.answer("⛔ Sizda usta huquqi yo'q.")
        await msg.answer("🔨 <b>Usta Panel</b>", reply_markup=usta_main_kb())
    except Exception:
        logger.exception("open_usta_panel xatosi")


# ──────────────────────────────────────────
# 🔇 Noop (bo'sh callback tugmalar uchun)
# ──────────────────────────────────────────
@router.callback_query(F.data == "noop")
async def noop_cb(call):
    await call.answer()


# ══════════════════════════════════════════════════════════════
# 📂 6. ADMIN: BO'LIMLAR (CATEGORIES) CRUD
# ══════════════════════════════════════════════════════════════
async def _require_admin(obj) -> bool:
    tg_id = obj.from_user.id
    return await get_staff_role(tg_id) == "admin"


@router.message(F.text == "📂 Bo'limlar")
async def categories_menu(msg: Message) -> None:
    try:
        if not await _require_admin(msg):
            return
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        await msg.answer(
            "📂 <b>Bo'limlar ro'yxati.</b>\nTahrirlash uchun bo'limni tanlang:",
            reply_markup=categories_admin_kb(cats),
        )
    except Exception:
        logger.exception("categories_menu xatosi")


@router.callback_query(F.data == "admcat_back")
async def admcat_back(call: CallbackQuery) -> None:
    try:
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        await call.message.edit_text(
            "📂 <b>Bo'limlar ro'yxati.</b>\nTahrirlash uchun bo'limni tanlang:",
            reply_markup=categories_admin_kb(cats),
        )
        await call.answer()
    except Exception:
        logger.exception("admcat_back xatosi")


@router.callback_query(F.data == "admcat_new")
async def admcat_new(call: CallbackQuery, state: FSMContext) -> None:
    try:
        if not await _require_admin(call):
            return await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        await call.message.answer("📂 Yangi bo'lim nomini kiriting:", reply_markup=CANCEL_KB)
        await state.set_state(CategoryFSM.add_name)
        await call.answer()
    except Exception:
        logger.exception("admcat_new xatosi")


@router.message(CategoryFSM.add_name)
async def save_new_category(msg: Message, state: FSMContext) -> None:
    try:
        name = (msg.text or "").strip()
        if len(name) < 2:
            return await msg.answer("⚠️ Bo'lim nomi kamida 2 harfdan iborat bo'lsin:")
        await execute("INSERT INTO categories(name) VALUES(?)", (name,))
        await state.clear()
        await msg.answer(f"✅ Bo'lim qo'shildi: <b>{name}</b>", reply_markup=admin_main_kb())
    except Exception:
        logger.exception("save_new_category xatosi")


@router.callback_query(F.data.startswith("admcat_") & F.data.regexp(r"^admcat_\d+$"))
async def category_detail(call: CallbackQuery) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        cat = await fetch_one("SELECT * FROM categories WHERE id=?", (cat_id,))
        if not cat:
            return await call.answer("Topilmadi.", show_alert=True)
        count = await fetch_one("SELECT COUNT(*) as c FROM products WHERE category_id=?", (cat_id,))
        await call.message.edit_text(
            f"📂 <b>{cat['name']}</b>\n📦 Mahsulotlar soni: {count['c']}",
            reply_markup=category_actions_kb(cat_id),
        )
        await call.answer()
    except Exception:
        logger.exception("category_detail xatosi")


@router.callback_query(F.data.startswith("admcatedit_"))
async def admcatedit_start(call: CallbackQuery, state: FSMContext) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        await state.update_data(edit_cat_id=cat_id)
        await call.message.answer("✏️ Bo'limning yangi nomini kiriting:", reply_markup=CANCEL_KB)
        await state.set_state(CategoryFSM.edit_name)
        await call.answer()
    except Exception:
        logger.exception("admcatedit_start xatosi")


@router.message(CategoryFSM.edit_name)
async def admcatedit_save(msg: Message, state: FSMContext) -> None:
    try:
        name = (msg.text or "").strip()
        if len(name) < 2:
            return await msg.answer("⚠️ To'g'ri nom kiriting:")
        data = await state.get_data()
        await execute("UPDATE categories SET name=? WHERE id=?", (name, data["edit_cat_id"]))
        await state.clear()
        await msg.answer(f"✅ Bo'lim nomi yangilandi: <b>{name}</b>", reply_markup=admin_main_kb())
    except Exception:
        logger.exception("admcatedit_save xatosi")


@router.callback_query(F.data.startswith("admcatdel_"))
async def admcatdel_confirm(call: CallbackQuery) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        await call.message.edit_text(
            "⚠️ <b>Diqqat!</b> Bo'limni o'chirsangiz, unga tegishli "
            "<b>barcha mahsulotlar ham o'chiriladi</b>. Davom etasizmi?",
            reply_markup=confirm_kb(f"admcatdelyes_{cat_id}", "admcat_back"),
        )
        await call.answer()
    except Exception:
        logger.exception("admcatdel_confirm xatosi")


@router.callback_query(F.data.startswith("admcatdelyes_"))
async def admcatdel_execute(call: CallbackQuery) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        # SQLite'da FOREIGN KEY ON DELETE CASCADE PRAGMA yoqilgan bo'lsa avtomatik o'chadi,
        # lekin ishonchli bo'lish uchun mahsulotlarni ham aniq o'chiramiz.
        await execute("DELETE FROM products WHERE category_id=?", (cat_id,))
        await execute("DELETE FROM categories WHERE id=?", (cat_id,))
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        await call.message.edit_text(
            "✅ Bo'lim va unga tegishli mahsulotlar o'chirildi.\n\n📂 <b>Bo'limlar ro'yxati:</b>",
            reply_markup=categories_admin_kb(cats),
        )
        await call.answer()
    except Exception:
        logger.exception("admcatdel_execute xatosi")
        await call.answer("⚠️ Xatolik yuz berdi.", show_alert=True)


# ══════════════════════════════════════════════════════════════
# 📦 7. ADMIN: MAHSULOTLAR (PRODUCTS) CRUD
# ══════════════════════════════════════════════════════════════
FIELD_LABELS = {"name": "nomi", "desc": "tavsifi", "price": "narxi", "photo": "rasmi"}


async def _require_admin(obj) -> bool:
    return await get_staff_role(obj.from_user.id) == "admin"


# ──────────────────────────────────────────
# 📦 Mahsulotlar bo'yicha bo'lim tanlash
# ──────────────────────────────────────────
@router.message(F.text == "📦 Mahsulotlar")
async def products_menu(msg: Message) -> None:
    try:
        if not await _require_admin(msg):
            return
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        if not cats:
            return await msg.answer("⚠️ Avval kamida bitta bo'lim (category) qo'shing.")
        await msg.answer(
            "📂 Mahsulot qo'shmoqchi/tahrirlamoqchi bo'lgan bo'limni tanlang:",
            reply_markup=categories_pick_kb(cats, "prodcat"),
        )
    except Exception:
        logger.exception("products_menu xatosi")


@router.callback_query(F.data.startswith("prodcat_"))
async def show_products_in_category(call: CallbackQuery) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        products = await fetch_all("SELECT * FROM products WHERE category_id=? ORDER BY id", (cat_id,))
        await call.message.edit_text(
            "📦 <b>Mahsulotlar:</b>", reply_markup=products_admin_kb(products, cat_id)
        )
        await call.answer()
    except Exception:
        logger.exception("show_products_in_category xatosi")


# ──────────────────────────────────────────
# ➕ Yangi mahsulot qo'shish
# ──────────────────────────────────────────
@router.callback_query(F.data.startswith("admprodnew_"))
async def new_product_start(call: CallbackQuery, state: FSMContext) -> None:
    try:
        if not await _require_admin(call):
            return await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        cat_id = int(call.data.split("_")[1])
        await state.update_data(new_category_id=cat_id)
        await call.message.answer("🪑 Mahsulot nomini kiriting:", reply_markup=CANCEL_KB)
        await state.set_state(ProductFSM.name)
        await call.answer()
    except Exception:
        logger.exception("new_product_start xatosi")


@router.message(ProductFSM.name)
async def new_product_name(msg: Message, state: FSMContext) -> None:
    try:
        name = (msg.text or "").strip()
        if len(name) < 2:
            return await msg.answer("⚠️ To'g'ri nom kiriting:")
        await state.update_data(name=name)
        await msg.answer("💬 Mahsulot tavsifini kiriting (masalan, o'lchamlari, materiali):", reply_markup=CANCEL_KB)
        await state.set_state(ProductFSM.description)
    except Exception:
        logger.exception("new_product_name xatosi")


@router.message(ProductFSM.description)
async def new_product_desc(msg: Message, state: FSMContext) -> None:
    try:
        await state.update_data(description=(msg.text or "").strip())
        await msg.answer(
            "💰 Narxini kiriting (matn ko'rinishida, masalan: <i>2 500 000 so'm</i> yoki "
            "<i>Kelishilgan narxda</i>):",
            reply_markup=CANCEL_KB,
        )
        await state.set_state(ProductFSM.price)
    except Exception:
        logger.exception("new_product_desc xatosi")


@router.message(ProductFSM.price)
async def new_product_price(msg: Message, state: FSMContext) -> None:
    try:
        price = (msg.text or "").strip()
        if not price:
            return await msg.answer("⚠️ Narxni kiriting:")
        await state.update_data(price=price)
        await msg.answer(
            "🖼 Mahsulot rasmini yuboring (yoki rasmsiz qoldirishingiz mumkin):",
            reply_markup=skip_photo_kb(),
        )
        await state.set_state(ProductFSM.photo)
    except Exception:
        logger.exception("new_product_price xatosi")


@router.message(ProductFSM.photo, F.photo)
async def new_product_photo(msg: Message, state: FSMContext) -> None:
    try:
        await _finish_new_product(msg, state, photo_id=msg.photo[-1].file_id)
    except Exception:
        logger.exception("new_product_photo xatosi")


@router.callback_query(F.data == "admprod_skipphoto", ProductFSM.photo)
async def new_product_skip_photo(call: CallbackQuery, state: FSMContext) -> None:
    try:
        await _finish_new_product(call.message, state, photo_id=None)
        await call.answer()
    except Exception:
        logger.exception("new_product_skip_photo xatosi")


async def _finish_new_product(msg: Message, state: FSMContext, photo_id: str | None) -> None:
    data = await state.get_data()
    await execute(
        "INSERT INTO products(category_id, name, description, price, photo_id) VALUES(?,?,?,?,?)",
        (data["new_category_id"], data["name"], data.get("description", ""), data["price"], photo_id),
    )
    await state.clear()
    await msg.answer(f"✅ Mahsulot qo'shildi: <b>{data['name']}</b>", reply_markup=admin_main_kb())


# ──────────────────────────────────────────
# 🔍 Mahsulot tafsilotlari va tahrirlash
# ──────────────────────────────────────────
@router.callback_query(F.data.startswith("admprod_") & F.data.regexp(r"^admprod_\d+$"))
async def product_detail(call: CallbackQuery) -> None:
    try:
        prod_id = int(call.data.split("_")[1])
        p = await fetch_one("SELECT * FROM products WHERE id=?", (prod_id,))
        if not p:
            return await call.answer("Topilmadi.", show_alert=True)
        text = f"🪑 <b>{p['name']}</b>\n\n{p['description'] or '—'}\n\n💰 {p['price']}"
        try:
            await call.message.delete()
        except Exception:
            pass
        if p["photo_id"]:
            await call.message.answer_photo(p["photo_id"], caption=text, reply_markup=product_actions_kb(prod_id))
        else:
            await call.message.answer(text, reply_markup=product_actions_kb(prod_id))
        await call.answer()
    except Exception:
        logger.exception("product_detail xatosi")


@router.callback_query(F.data.startswith("admprodedit_"))
async def product_edit_start(call: CallbackQuery, state: FSMContext) -> None:
    try:
        # data format: admprodedit_<field>_<id>
        parts = call.data.split("_")
        field, prod_id = parts[1], int(parts[2])
        await state.update_data(edit_prod_id=prod_id, edit_field=field)

        prompts = {
            "name": "🪑 Yangi nomni kiriting:",
            "desc": "💬 Yangi tavsifni kiriting:",
            "price": "💰 Yangi narxni kiriting:",
            "photo": "🖼 Yangi rasmni yuboring:",
        }
        await call.message.answer(prompts.get(field, "Yangi qiymatni kiriting:"), reply_markup=CANCEL_KB)
        await state.set_state(ProductFSM.edit_field_value)
        await call.answer()
    except Exception:
        logger.exception("product_edit_start xatosi")


@router.message(ProductFSM.edit_field_value, F.photo)
async def product_edit_save_photo(msg: Message, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        if data.get("edit_field") != "photo":
            return await msg.answer("⚠️ Iltimos, matn kiriting (rasm emas).")
        await execute("UPDATE products SET photo_id=? WHERE id=?", (msg.photo[-1].file_id, data["edit_prod_id"]))
        await _finish_edit(msg, state)
    except Exception:
        logger.exception("product_edit_save_photo xatosi")


@router.message(ProductFSM.edit_field_value)
async def product_edit_save_text(msg: Message, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        field = data.get("edit_field")
        if field == "photo":
            return await msg.answer("⚠️ Iltimos, rasm yuboring.")

        value = (msg.text or "").strip()
        if not value:
            return await msg.answer("⚠️ Bo'sh qiymat kiritib bo'lmaydi:")

        column = {"name": "name", "desc": "description", "price": "price"}.get(field)
        if not column:
            return await msg.answer("⚠️ Noma'lum maydon.")

        await execute(f"UPDATE products SET {column}=? WHERE id=?", (value, data["edit_prod_id"]))
        await _finish_edit(msg, state)
    except Exception:
        logger.exception("product_edit_save_text xatosi")


async def _finish_edit(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("✅ Mahsulot muvaffaqiyatli yangilandi!", reply_markup=admin_main_kb())


# ──────────────────────────────────────────
# 🗑 Mahsulotni o'chirish
# ──────────────────────────────────────────
@router.callback_query(F.data.startswith("admproddel_"))
async def product_delete_confirm(call: CallbackQuery) -> None:
    try:
        prod_id = int(call.data.split("_")[1])
        await call.message.answer(
            "⚠️ Mahsulotni o'chirishni tasdiqlaysizmi?",
            reply_markup=confirm_kb(f"admproddelyes_{prod_id}", "admcat_back"),
        )
        await call.answer()
    except Exception:
        logger.exception("product_delete_confirm xatosi")


@router.callback_query(F.data.startswith("admproddelyes_"))
async def product_delete_execute(call: CallbackQuery) -> None:
    try:
        prod_id = int(call.data.split("_")[1])
        await execute("DELETE FROM products WHERE id=?", (prod_id,))
        await call.message.answer("✅ Mahsulot o'chirildi.", reply_markup=admin_main_kb())
        await call.answer()
    except Exception:
        logger.exception("product_delete_execute xatosi")


# ══════════════════════════════════════════════════════════════
# 👥 8. ADMIN: XODIMLAR BOSHQARUVI
# ══════════════════════════════════════════════════════════════
async def _require_admin(obj) -> bool:
    return await get_staff_role(obj.from_user.id) == "admin"


@router.message(F.text == "👥 Xodimlar")
async def staff_menu(msg: Message) -> None:
    try:
        if not await _require_admin(msg):
            return
        staff = await fetch_all("SELECT * FROM staff ORDER BY role, id")
        await msg.answer(
            "👥 <b>Xodimlar ro'yxati.</b>\nO'chirish uchun xodimni tanlang yoki yangisini qo'shing:",
            reply_markup=staff_list_kb(staff),
        )
    except Exception:
        logger.exception("staff_menu xatosi")


@router.callback_query(F.data == "staff_add")
async def staff_add_start(call: CallbackQuery, state: FSMContext) -> None:
    try:
        if not await _require_admin(call):
            return await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        await call.message.answer(
            "🆔 Yangi xodimning Telegram ID raqamini kiriting:\n"
            "<i>(ID'ni bilish uchun xodim @userinfobot ga /start yozishi mumkin)</i>",
            reply_markup=CANCEL_KB,
        )
        await state.set_state(StaffFSM.enter_id)
        await call.answer()
    except Exception:
        logger.exception("staff_add_start xatosi")


@router.message(StaffFSM.enter_id)
async def staff_add_id(msg: Message, state: FSMContext) -> None:
    try:
        text = (msg.text or "").strip()
        if not text.isdigit():
            return await msg.answer("⚠️ Iltimos, faqat raqamlardan iborat ID kiriting:")
        tg_id = int(text)

        existing = await fetch_one("SELECT * FROM staff WHERE tg_id=?", (tg_id,))
        if existing:
            return await msg.answer(f"⚠️ Bu foydalanuvchi allaqachon xodim ({existing['role']}).")

        await state.update_data(new_staff_id=tg_id)
        await msg.answer("👤 Ushbu xodimga qaysi rolni berasiz?", reply_markup=role_choice_kb())
        await state.set_state(StaffFSM.choose_role)
    except Exception:
        logger.exception("staff_add_id xatosi")


@router.callback_query(F.data.in_({"role_admin", "role_usta"}), StaffFSM.choose_role)
async def staff_add_role(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    try:
        role = "admin" if call.data == "role_admin" else "usta"
        data = await state.get_data()
        tg_id = data["new_staff_id"]

        full_name = None
        try:
            chat = await bot.get_chat(tg_id)
            full_name = chat.full_name
        except Exception:
            pass  # Foydalanuvchi hali bot bilan aloqa qilmagan bo'lishi mumkin

        await execute(
            "INSERT INTO staff(tg_id, full_name, role) VALUES(?, ?, ?)",
            (tg_id, full_name, role),
        )
        await state.clear()

        role_label = "🛡 Admin" if role == "admin" else "🔨 Usta"
        await call.message.answer(f"✅ Xodim qo'shildi: <code>{tg_id}</code> — {role_label}", reply_markup=admin_main_kb())

        try:
            await bot.send_message(tg_id, f"🎉 Sizga <b>{role_label}</b> huquqi berildi! /start buyrug'ini bosing.")
        except Exception:
            logger.warning("Yangi xodimga (%s) xabar yuborib bo'lmadi.", tg_id)

        await call.answer()
    except Exception:
        logger.exception("staff_add_role xatosi")
        await call.answer("⚠️ Xatolik yuz berdi.", show_alert=True)


@router.callback_query(F.data.startswith("staffdel_"))
async def staff_delete(call: CallbackQuery) -> None:
    try:
        if not await _require_admin(call):
            return await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        tg_id = int(call.data.split("_")[1])
        await execute("DELETE FROM staff WHERE tg_id=?", (tg_id,))
        staff = await fetch_all("SELECT * FROM staff ORDER BY role, id")
        await call.message.edit_text(
            "✅ Xodim o'chirildi.\n\n👥 <b>Xodimlar ro'yxati:</b>", reply_markup=staff_list_kb(staff)
        )
        await call.answer()
    except Exception:
        logger.exception("staff_delete xatosi")


# ══════════════════════════════════════════════════════════════
# ⚙️ 9. ADMIN: USTAXONA SOZLAMALARI (Ochiq/Yopiq)
# ══════════════════════════════════════════════════════════════
def _settings_kb(open_now: bool):
    label = "🔴 Yopish" if open_now else "🟢 Ochish"
    return ik([(label, "toggle_shop_status")])


async def _require_admin(obj) -> bool:
    return await get_staff_role(obj.from_user.id) == "admin"


@router.message(F.text == "⚙️ Ustaxona holati")
async def settings_menu(msg: Message) -> None:
    try:
        if not await _require_admin(msg):
            return
        open_now = await is_shop_open()
        status = "🟢 Hozir ochiq" if open_now else "🔴 Hozir yopiq"
        await msg.answer(
            f"⚙️ <b>Ustaxona holati:</b> {status}\n\n"
            "Yopiq holatda mijozlar yangi buyurtma bera olmaydi.",
            reply_markup=_settings_kb(open_now),
        )
    except Exception:
        logger.exception("settings_menu xatosi")


@router.callback_query(F.data == "toggle_shop_status")
async def toggle_status(call: CallbackQuery) -> None:
    try:
        if not await _require_admin(call):
            return await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        open_now = await is_shop_open()
        new_value = "0" if open_now else "1"
        await execute("UPDATE settings SET value=? WHERE key='is_open'", (new_value,))

        status = "🟢 Hozir ochiq" if new_value == "1" else "🔴 Hozir yopiq"
        await call.message.edit_text(
            f"⚙️ <b>Ustaxona holati:</b> {status}\n\n"
            "Yopiq holatda mijozlar yangi buyurtma bera olmaydi.",
            reply_markup=_settings_kb(new_value == "1"),
        )
        await call.answer("✅ Holat yangilandi!")
    except Exception:
        logger.exception("toggle_status xatosi")
        await call.answer("⚠️ Xatolik yuz berdi.", show_alert=True)


# ══════════════════════════════════════════════════════════════
# 📨 10. ADMIN: XABAR YUBORISH (BROADCAST)
# ══════════════════════════════════════════════════════════════
async def _require_admin(obj) -> bool:
    return await get_staff_role(obj.from_user.id) == "admin"


@router.message(F.text == "📨 Xabar yuborish")
async def broadcast_start(msg: Message, state: FSMContext) -> None:
    try:
        if not await _require_admin(msg):
            return
        await msg.answer(
            "📨 Barcha mijozlarga yuboriladigan xabarni yozing (matn) yoki rasm yuboring (izoh bilan):",
            reply_markup=CANCEL_KB,
        )
        await state.set_state(BroadcastFSM.content)
    except Exception:
        logger.exception("broadcast_start xatosi")


@router.message(BroadcastFSM.content)
async def broadcast_send(msg: Message, state: FSMContext, bot: Bot) -> None:
    try:
        users = await fetch_all("SELECT tg_id FROM users WHERE is_blocked=0")
        await state.clear()
        await msg.answer(f"⏳ Xabar {len(users)} ta mijozga yuborilmoqda...")

        sent, failed = 0, 0
        for u in users:
            try:
                if msg.photo:
                    await bot.send_photo(u["tg_id"], msg.photo[-1].file_id, caption=msg.caption or "")
                elif msg.text:
                    await bot.send_message(u["tg_id"], msg.text)
                sent += 1
            except Exception:
                failed += 1
                await execute("UPDATE users SET is_blocked=1 WHERE tg_id=?", (u["tg_id"],))
            await asyncio.sleep(0.05)  # Telegram limitlariga (flood control) hurmat

        await msg.answer(
            f"✅ <b>Xabar yuborish yakunlandi!</b>\n\n📤 Yuborildi: {sent} ta\n🚫 Yetkazilmadi: {failed} ta",
            reply_markup=admin_main_kb(),
        )
    except Exception:
        logger.exception("broadcast_send xatosi")


# ══════════════════════════════════════════════════════════════
# 🔨 11. USTA PANELI
# ══════════════════════════════════════════════════════════════
async def _is_usta(tg_id: int) -> bool:
    row = await fetch_one("SELECT 1 FROM staff WHERE tg_id=? AND role='usta'", (tg_id,))
    return bool(row)


@router.message(F.text == "📋 Yangi buyurtmalar")
async def list_new_orders(msg: Message) -> None:
    try:
        if not await _is_usta(msg.from_user.id):
            return
        orders = await fetch_all("SELECT * FROM orders WHERE status='new' ORDER BY id DESC")
        if not orders:
            return await msg.answer("📭 Hozircha yangi buyurtmalar yo'q.")

        for o in orders:
            maps_link = f"https://maps.google.com/?q={o['lat']},{o['lon']}"
            phone2_line = f"\n📞 Qo'shimcha: {o['phone2']}" if o["phone2"] else ""
            text = (
                f"🆕 <b>Buyurtma #{o['id']}</b>\n\n"
                f"👤 {o['customer_name']}\n"
                f"📱 {o['phone']}{phone2_line}\n"
                f"🪑 {o['product_name']} ({o['product_price']})\n"
                f"📍 <a href='{maps_link}'>Lokatsiya</a>"
            )
            await msg.answer(text, reply_markup=usta_accept_kb(o["id"]))
    except Exception:
        logger.exception("list_new_orders xatosi")


@router.message(F.text == "✅ Mening buyurtmalarim")
async def list_my_orders(msg: Message) -> None:
    try:
        if not await _is_usta(msg.from_user.id):
            return
        orders = await fetch_all(
            "SELECT * FROM orders WHERE usta_tg_id=? ORDER BY id DESC LIMIT 15", (msg.from_user.id,)
        )
        if not orders:
            return await msg.answer("📭 Siz hali buyurtma qabul qilmagansiz.")
        lines = ["✅ <b>Siz qabul qilgan buyurtmalar:</b>\n"]
        for o in orders:
            lines.append(f"#{o['id']} • {o['product_name']} • {o['customer_name']} • {o['phone']}")
        await msg.answer("\n".join(lines))
    except Exception:
        logger.exception("list_my_orders xatosi")


@router.callback_query(F.data.startswith("accept_"))
async def accept_order(call: CallbackQuery, bot: Bot) -> None:
    """Usta buyurtmani qabul qilganda ishga tushadi."""
    try:
        if not await _is_usta(call.from_user.id):
            return await call.answer("⛔ Bu amal faqat ustalar uchun.", show_alert=True)

        order_id = int(call.data.split("_")[1])
        order = await fetch_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not order:
            return await call.answer("Buyurtma topilmadi.", show_alert=True)

        if order["status"] != "new":
            await call.answer("⚠️ Bu buyurtma allaqachon qabul qilingan.", show_alert=True)
            try:
                await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        usta = await fetch_one("SELECT full_name FROM staff WHERE tg_id=?", (call.from_user.id,))
        usta_name = usta["full_name"] if usta and usta["full_name"] else call.from_user.full_name

        await execute(
            "UPDATE orders SET status='accepted', usta_tg_id=?, usta_name=? WHERE id=?",
            (call.from_user.id, usta_name, order_id),
        )

        # Qabul qilgan ustaning xabarini yangilaymiz
        try:
            await call.message.edit_reply_markup(reply_markup=None)
            await call.message.answer(f"✅ Siz buyurtma #{order_id} ni qabul qildingiz.")
        except Exception:
            pass

        # Mijozga xabar beramiz
        try:
            await bot.send_message(
                order["customer_tg_id"],
                f"🔨 <b>Buyurtmangiz #{order_id}</b> ustaxonamizning ustasi "
                f"<b>{usta_name}</b> tomonidan qabul qilindi. Tez orada siz bilan bog'lanishadi!",
            )
        except Exception:
            logger.warning("Mijozga (%s) xabar yuborib bo'lmadi.", order["customer_tg_id"])

        # Boshqa ustalarga bu buyurtma band bo'lganini bildiramiz
        other_ustas = await fetch_all(
            "SELECT tg_id FROM staff WHERE role='usta' AND tg_id != ?", (call.from_user.id,)
        )
        for u in other_ustas:
            try:
                await bot.send_message(
                    u["tg_id"], f"ℹ️ Buyurtma #{order_id} boshqa usta ({usta_name}) tomonidan qabul qilindi."
                )
            except Exception:
                pass

        await call.answer("✅ Buyurtma sizga biriktirildi!")
    except Exception:
        logger.exception("accept_order xatosi")
        await call.answer("⚠️ Xatolik yuz berdi.", show_alert=True)


# ══════════════════════════════════════════════════════════════
# 🛍 12. MIJOZ: KATALOG VA BUYURTMA JARAYONI
# ══════════════════════════════════════════════════════════════
STATUS_LABELS = {
    "new": "⏳ Ko'rib chiqilmoqda",
    "accepted": "🔨 Usta qabul qildi",
}


# ──────────────────────────────────────────
# 🛍 1. KATALOG (grid ko'rinishida)
# ──────────────────────────────────────────
@router.message(F.text == "🛍 Katalog")
async def cmd_catalog(msg: Message) -> None:
    try:
        user = await fetch_one("SELECT * FROM users WHERE tg_id=?", (msg.from_user.id,))
        if not user:
            return await msg.answer("Iltimos, avval /start buyrug'ini bosing.")
        if user["is_blocked"]:
            return await msg.answer("❌ Kechirasiz, sizga botdan foydalanish taqiqlangan.")

        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        if not cats:
            return await msg.answer("😔 Hozircha bo'limlar mavjud emas.")
        await msg.answer("📂 <b>Bo'limni tanlang:</b>", reply_markup=catalog_categories_kb(cats))
    except Exception:
        logger.exception("cmd_catalog xatosi")
        await msg.answer("⚠️ Xatolik yuz berdi.")


@router.callback_query(F.data.startswith("cat_"))
async def show_category_products(call: CallbackQuery) -> None:
    try:
        cat_id = int(call.data.split("_")[1])
        products = await fetch_all(
            "SELECT * FROM products WHERE category_id=? AND is_active=1 ORDER BY id", (cat_id,)
        )
        if not products:
            return await call.answer("Bu bo'limda hozircha mahsulot yo'q.", show_alert=True)
        await call.message.edit_text(
            "🪑 <b>Mahsulotni tanlang:</b>", reply_markup=catalog_grid_kb(products, cat_id)
        )
        await call.answer()
    except Exception:
        logger.exception("show_category_products xatosi")
        await call.answer("⚠️ Xatolik.", show_alert=True)


@router.callback_query(F.data == "back_to_cats")
async def back_to_categories(call: CallbackQuery) -> None:
    try:
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        await call.message.edit_text("📂 <b>Bo'limni tanlang:</b>", reply_markup=catalog_categories_kb(cats))
        await call.answer()
    except Exception:
        logger.exception("back_to_categories xatosi")


@router.callback_query(F.data.startswith("prod_"))
async def show_product(call: CallbackQuery) -> None:
    try:
        prod_id = int(call.data.split("_")[1])
        p = await fetch_one("SELECT * FROM products WHERE id=? AND is_active=1", (prod_id,))
        if not p:
            return await call.answer("Mahsulot topilmadi.", show_alert=True)

        text = f"🪑 <b>{p['name']}</b>\n\n{p['description'] or ''}\n\n💰 Narxi: <b>{p['price']}</b>"
        kb = product_view_kb(prod_id)

        try:
            await call.message.delete()
        except Exception:
            pass

        if p["photo_id"]:
            await call.message.answer_photo(p["photo_id"], caption=text, reply_markup=kb)
        else:
            await call.message.answer(text, reply_markup=kb)
        await call.answer()
    except Exception:
        logger.exception("show_product xatosi")
        await call.answer("⚠️ Xatolik.", show_alert=True)


@router.callback_query(F.data == "back_to_prods")
async def back_to_products(call: CallbackQuery) -> None:
    """Mahsulot ko'rinishidan bo'limlar ro'yxatiga qaytish (soddalik uchun)."""
    try:
        cats = await fetch_all("SELECT * FROM categories ORDER BY id")
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer("📂 <b>Bo'limni tanlang:</b>", reply_markup=catalog_categories_kb(cats))
        await call.answer()
    except Exception:
        logger.exception("back_to_products xatosi")


# ──────────────────────────────────────────
# 🛒 2. BUYURTMA JARAYONI (FSM)
# ──────────────────────────────────────────
@router.callback_query(F.data.startswith("order_") & ~F.data.in_({"order_confirm", "order_cancel_flow"}))
async def start_order(call: CallbackQuery, state: FSMContext) -> None:
    """Mijoz 'Buyurtma berish' tugmasini bosganda jarayonni boshlaydi."""
    try:
        if not await is_shop_open():
            return await call.answer(
                "🔴 Kechirasiz, ustaxona hozir yopiq. Keyinroq urinib ko'ring.", show_alert=True
            )

        prod_id = int(call.data.split("_")[1])
        product = await fetch_one("SELECT * FROM products WHERE id=? AND is_active=1", (prod_id,))
        if not product:
            return await call.answer("Mahsulot topilmadi.", show_alert=True)

        await state.update_data(product_id=product["id"], product_name=product["name"], product_price=product["price"])
        await state.set_state(OrderFlow.phone)

        await call.message.answer(
            "📱 <b>Asosiy telefon raqamingizni</b> yuboring (tugma orqali yoki qo'lda yozing):",
            reply_markup=phone_request_kb(),
        )
        await call.answer()
    except Exception:
        logger.exception("start_order xatosi")
        await call.answer("⚠️ Xatolik yuz berdi.", show_alert=True)


@router.message(OrderFlow.phone)
async def order_get_phone(msg: Message, state: FSMContext) -> None:
    try:
        phone = msg.contact.phone_number if msg.contact else (msg.text or "").strip()
        if not phone or len(phone) < 7:
            return await msg.answer("⚠️ Iltimos, to'g'ri telefon raqam yuboring:")
        if not phone.startswith("+"):
            phone = "+" + phone.lstrip("+")

        await state.update_data(phone=phone)
        await state.set_state(OrderFlow.phone2)

        await msg.answer(
            "📞 <b>Qo'shimcha telefon raqami</b> bo'lsa yuboring "
            "(shart emas — o'tkazib yuborishingiz mumkin):",
            reply_markup=SKIP_OR_CANCEL_KB,
        )
    except Exception:
        logger.exception("order_get_phone xatosi")


@router.message(OrderFlow.phone2)
async def order_get_phone2(msg: Message, state: FSMContext) -> None:
    try:

        phone2 = None
        if msg.text != SKIP_TEXT:
            phone2 = msg.contact.phone_number if msg.contact else (msg.text or "").strip()
            if phone2 and not phone2.startswith("+"):
                phone2 = "+" + phone2.lstrip("+")

        await state.update_data(phone2=phone2)
        await state.set_state(OrderFlow.location)
        await msg.answer(
            "📍 Endi <b>lokatsiyangizni</b> yuboring (buyurtmani yetkazish/kelishish uchun):",
            reply_markup=location_request_kb(),
        )
    except Exception:
        logger.exception("order_get_phone2 xatosi")


@router.message(OrderFlow.location)
async def order_get_location(msg: Message, state: FSMContext) -> None:
    try:
        if not msg.location:
            return await msg.answer(
                "⚠️ Iltimos, pastdagi <b>📍 Lokatsiyani yuborish</b> tugmasidan foydalaning."
            )
        await state.update_data(lat=msg.location.latitude, lon=msg.location.longitude)
        data = await state.get_data()

        maps_link = f"https://maps.google.com/?q={data['lat']},{data['lon']}"
        phone2_line = f"\n📞 Qo'shimcha: {data['phone2']}" if data.get("phone2") else ""

        text = (
            "🧾 <b>Buyurtmangizni tasdiqlang:</b>\n\n"
            f"🪑 Mahsulot: <b>{data['product_name']}</b>\n"
            f"💰 Narxi: {data['product_price']}\n"
            f"📱 Telefon: {data['phone']}{phone2_line}\n"
            f"📍 <a href='{maps_link}'>Lokatsiya (xaritada ko'rish)</a>"
        )
        await msg.answer(text, reply_markup=order_confirm_kb(), disable_web_page_preview=False)
        await state.set_state(OrderFlow.confirm)
    except Exception:
        logger.exception("order_get_location xatosi")


@router.callback_query(F.data == "order_cancel_flow")
async def order_cancel_flow(call: CallbackQuery, state: FSMContext) -> None:
    try:
        await state.clear()
        kb = await main_kb_for(call.from_user.id)
        await call.message.answer("🚫 Buyurtma bekor qilindi.", reply_markup=kb)
        await call.answer()
    except Exception:
        logger.exception("order_cancel_flow xatosi")


@router.callback_query(F.data == "order_confirm", OrderFlow.confirm)
async def order_confirm(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Buyurtmani bazaga saqlaydi va barcha 'Usta'larga xabar yuboradi."""
    try:
        data = await state.get_data()
        user = await fetch_one("SELECT * FROM users WHERE tg_id=?", (call.from_user.id,))

        order_id = await execute(
            "INSERT INTO orders(customer_tg_id, customer_name, phone, phone2, product_id, "
            "product_name, product_price, lat, lon, status) VALUES(?,?,?,?,?,?,?,?,?,'new')",
            (
                call.from_user.id, user["full_name"], data["phone"], data.get("phone2"),
                data["product_id"], data["product_name"], data["product_price"],
                data["lat"], data["lon"],
            ),
        )
        await state.clear()

        kb = await main_kb_for(call.from_user.id)
        try:
            await call.message.edit_text(f"🎉 <b>Buyurtma #{order_id} qabul qilindi!</b>\nTez orada usta siz bilan bog'lanadi.")
        except Exception:
            pass
        await call.message.answer("🏠 Asosiy menyu", reply_markup=kb)

        # Barcha ustalarga xabar yuborish
        maps_link = f"https://maps.google.com/?q={data['lat']},{data['lon']}"
        phone2_line = f"\n📞 Qo'shimcha: {data['phone2']}" if data.get("phone2") else ""
        usta_text = (
            f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
            f"👤 Mijoz: {user['full_name']}\n"
            f"📱 Telefon: {data['phone']}{phone2_line}\n"
            f"🪑 Mahsulot: <b>{data['product_name']}</b> ({data['product_price']})\n"
            f"📍 <a href='{maps_link}'>Lokatsiyani xaritada ko'rish</a>"
        )
        ustas = await fetch_all("SELECT tg_id FROM staff WHERE role='usta'")
        for u in ustas:
            try:
                await bot.send_location(u["tg_id"], data["lat"], data["lon"])
                await bot.send_message(u["tg_id"], usta_text, reply_markup=usta_accept_kb(order_id))
            except Exception:
                logger.warning("Ustaga (%s) xabar yuborib bo'lmadi.", u["tg_id"])
        await call.answer()
    except Exception:
        logger.exception("order_confirm xatosi")
        await call.answer("⚠️ Xatolik yuz berdi, qaytadan urinib ko'ring.", show_alert=True)


# ──────────────────────────────────────────
# 📦 3. BUYURTMALARIM
# ──────────────────────────────────────────
@router.message(F.text == "📦 Buyurtmalarim")
async def my_orders(msg: Message) -> None:
    try:
        orders = await fetch_all(
            "SELECT * FROM orders WHERE customer_tg_id=? ORDER BY id DESC LIMIT 10", (msg.from_user.id,)
        )
        if not orders:
            return await msg.answer("📭 Sizda hali buyurtmalar mavjud emas.")

        lines = ["📦 <b>So'nggi buyurtmalaringiz:</b>\n"]
        for o in orders:
            status = STATUS_LABELS.get(o["status"], o["status"])
            usta_line = f" — Usta: {o['usta_name']}" if o["usta_name"] else ""
            lines.append(f"#{o['id']} • {o['product_name']} • {status}{usta_line}")
        await msg.answer("\n".join(lines))
    except Exception:
        logger.exception("my_orders xatosi")


# ══════════════════════════════════════════════════════════════
# 🔇 13. DARVOZABON (CATCH-ALL) — ENG OXIRIDA!
# ══════════════════════════════════════════════════════════════
@router.message(StateFilter("*"))
async def catch_all(msg: Message, state: FSMContext) -> None:
    try:
        current_state = await state.get_state()
        if current_state is None:
            role = await get_staff_role(msg.from_user.id)
            if role == "admin":
                kb = admin_main_kb()
            elif role == "usta":
                kb = usta_main_kb()
            else:
                kb = await main_kb_for(msg.from_user.id)
            await msg.answer("👇 Iltimos, pastdagi menyu tugmalaridan foydalaning.", reply_markup=kb)
        else:
            await msg.answer(
                f"⚠️ Iltimos, so'ralgan ma'lumotni to'g'ri kiriting.\n"
                f"<i>(Jarayonni bekor qilish uchun \"{CANCEL_TEXT}\" tugmasini bosing.)</i>"
            )
    except Exception:
        logger.exception("catch_all xatosi")


# ══════════════════════════════════════════════════════════════
# 🚀 14. ENGINE RUNNER — Botni ishga tushirish
# ══════════════════════════════════════════════════════════════
async def main() -> None:
    dp = Dispatcher(storage=MemoryStorage())

    # Ma'lumotlar bazasini tayyorlash (jadvallar, boshlang'ich sozlamalar)
    await init_db()

    # Yagona routerni ulash (barcha handlerlar shu bitta routerda,
    # shuning uchun ular yozilgan tartibda ishlaydi — catch-all eng oxirida)
    dp.include_router(router)

    await bot.set_my_commands([BotCommand(command="start", description="Botni ishga tushirish")])

    logger.info("🚀 %s boti ishga tushdi...", SHOP_NAME)
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Bot to'xtatildi.")
