import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaPhoto, FSInputFile,
)

# ============================== CONFIG ======================================

BOT_TOKEN = "8919365987:AAHsSGcZaBJXN9hs-FMy_t_3OB6pUi_e3cg"
ADMIN_IDS = [8488028783]          # Admin Telegram ID
WORKER_GROUP_ID = None            # Ustalar guruhining chat_id si
DB_PATH = "gold_mebel.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gold_mebel")

# ============================== BOT & ROUTER INIT ===========================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ============================== MIDDLEWARES =================================

class ThrottleMiddleware(BaseMiddleware):
    def __init__(self):
        self.last = {}

    async def __call__(self, handler, event: CallbackQuery, data):
        uid = event.from_user.id
        now = time.monotonic()
        if now - self.last.get(uid, 0) < 0.6:
            await event.answer("Iltimos, biroz kuting...", show_alert=False)
            return
        self.last[uid] = now
        return await handler(event, data)

router.callback_query.middleware(ThrottleMiddleware())

# ============================== FSM STATES ===================================

class Reg(StatesGroup):
    name = State()
    phone = State()

class EditProfile(StatesGroup):
    name = State()
    phone = State()

class AdminCat(StatesGroup):
    add_name = State()
    edit_name = State()

class AdminProd(StatesGroup):
    name = State()
    desc = State()
    qty = State()
    price = State()
    new_price = State()
    photos = State()
    edit_value = State()

class OrderFlow(StatesGroup):
    location = State()
    comment = State()

class CustomOrder(StatesGroup):
    photo = State()
    desc = State()

class AdminSettingsFSM(StatesGroup):
    phone = State()
    location = State()
    help_text = State()

class AdminBroadcastFSM(StatesGroup):
    message = State()

class AdminUserSearchFSM(StatesGroup):
    query = State()


# ============================== DATABASE ====================================

db: aiosqlite.Connection = None

async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            role TEXT DEFAULT 'user',       
            banned INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            sku TEXT,
            name TEXT,
            description TEXT,
            price REAL,             
            old_price REAL,         
            quantity INTEGER DEFAULT 0,
            photos TEXT DEFAULT '[]',   
            is_top INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            qty INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            UNIQUE(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            user_id INTEGER,
            total REAL,
            status TEXT DEFAULT 'yangi',   
            comment TEXT,
            lat REAL,
            lon REAL,
            distance_km REAL,
            worker_id INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_id INTEGER,
            name TEXT,
            qty INTEGER,
            price REAL,
            item_type TEXT   
        );

        CREATE TABLE IF NOT EXISTS notify_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            UNIQUE(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_file_id TEXT,
            caption TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS msg_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_chat_id INTEGER,
            admin_msg_id INTEGER,
            customer_id INTEGER,
            created_at TEXT
        );
        """
    )
    defaults = {
        "workshop_lat": "41.311081",
        "workshop_lon": "69.240562",
        "workshop_phone": "+998901234567",
        "help_text": "Savollaringiz bo'lsa shu yerga yozing, tez orada javob beramiz.",
        "is_open": "1",
    }
    for k, v in defaults.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", (k, v)
        )
    await db.commit()

async def get_setting(key: str) -> str:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None

async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db.commit()

# ============================== UTILS =======================================

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def fmt_distance(km: float) -> str:
    if km < 1:
        return f"{int(km * 1000)} m"
    return f"{km:.1f} km"

def fmt_price(price):
    if price is None:
        return "Kelishiladi 🤝"
    return f"{int(price):,} so'm".replace(",", " ")

def maps_link(lat, lon) -> str:
    return f"https://www.google.com/maps?q={lat},{lon}"

async def is_admin(tg_id: int) -> bool:
    if tg_id in ADMIN_IDS:
        return True
    cur = await db.execute("SELECT role FROM users WHERE tg_id=?", (tg_id,))
    row = await cur.fetchone()
    return bool(row and row["role"] == "admin")

async def is_worker(tg_id: int) -> bool:
    if await is_admin(tg_id):
        return True
    cur = await db.execute("SELECT role FROM users WHERE tg_id=?", (tg_id,))
    row = await cur.fetchone()
    return bool(row and row["role"] == "worker")

async def get_user(tg_id: int):
    cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    return await cur.fetchone()

async def gen_order_code() -> str:
    import random
    return f"#B-{random.randint(1000, 9999)}"

# ============================== KEYBOARDS ====================================

def kb_phone():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
    )

def kb_location():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)],
                  [KeyboardButton(text="⛔️ Bekor qilish")]],
        resize_keyboard=True,
    )

async def kb_main(tg_id: int):
    rows = [
        ["🗂 Katalog", "🛒 Savat"],
        ["❤️ Tanlanganlar", "📦 Buyurtmalarim"],
        ["🖼 Portfolio", "📐 O'z loyihamni yuborish"],
        ["📍 Bizning manzil", "☎️ Qo'ng'iroq so'rash"],
        ["🆘 Yordam", "👤 Profil"],
    ]
    if await is_worker(tg_id):
        rows.append(["🛠 Usta paneli"])
    if await is_admin(tg_id):
        rows.append(["⚙️ Admin panel"])
    kb = [[KeyboardButton(text=t) for t in row] for row in rows]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def kb_admin():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗂 Bo'limlar boshqaruvi"), KeyboardButton(text="📦 Ombor nazorati")],
            [KeyboardButton(text="👥 Mijozlar bo'limi"), KeyboardButton(text="👨‍💼 Xodimlar")], 
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📣 Xabar yuborish")],
            [KeyboardButton(text="🖼 Portfolio"), KeyboardButton(text="⚙️ Sozlamalar")],
            [KeyboardButton(text="🏠 Asosiy menyu")]
        ],
        resize_keyboard=True
    )

def ikb(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=cd) for (t, cd) in row]
            for row in rows
        ]
    )

# ============================== HANDLERS =====================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject = None):
    await state.clear()
    user = await get_user(message.from_user.id)
    if user and user["banned"]:
        await message.answer("Kechirasiz, sizga botdan foydalanish cheklangan.")
        return
    if not user:
        await state.set_state(Reg.name)
        await message.answer(
            "Assalomu alaykum! <b>Gold Mebel</b> botiga xush kelibsiz.\n\n"
            "Ro'yxatdan o'tish uchun ismingizni yozing:"
        )
        return
    await message.answer(
        f"Xush kelibsiz, {user['name']}!", reply_markup=await kb_main(message.from_user.id)
    )
    if command and command.args and command.args.startswith("product_"):
        try:
            pid = int(command.args.split("_", 1)[1])
            await show_product(message.chat.id, message.from_user.id, pid, 0)
        except Exception:
            pass

@router.message(F.text == "🏠 Asosiy menyu")
async def back_to_main(message: Message, state: FSMContext):
    await cmd_start(message, state)

@router.message(Reg.name)
async def reg_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(Reg.phone)
    await message.answer("Endi telefon raqamingizni yuboring:", reply_markup=kb_phone())

@router.message(Reg.phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext):
    await _finish_reg(message, state, message.contact.phone_number)

@router.message(Reg.phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext):
    await _finish_reg(message, state, message.text.strip())

async def _finish_reg(message: Message, state: FSMContext, phone: str):
    data = await state.get_data()
    name = data.get("name", message.from_user.full_name)
    role = "admin" if message.from_user.id in ADMIN_IDS else "user"
    await db.execute(
        "INSERT INTO users(tg_id, name, phone, role, created_at) VALUES (?,?,?,?,?)",
        (message.from_user.id, name, phone, role, datetime.now().isoformat()),
    )
    await db.commit()
    await state.clear()
    await message.answer(
        "✅ Ro'yxatdan muvaffaqiyatli o'tdingiz!",
        reply_markup=await kb_main(message.from_user.id),
    )

@router.message(F.text == "👤 Profil")
async def profile_menu(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        return
    await message.answer(
        f"👤 <b>Profilingiz</b>\n\n"
        f"Ism: {user['name']}\n"
        f"Telefon: {user['phone']}\n"
        f"ID: <code>{user['tg_id']}</code>",
        reply_markup=ikb([
            [("✏️ Ismni o'zgartirish", "editprofile:name")],
            [("✏️ Telefonni o'zgartirish", "editprofile:phone")],
        ]),
    )

@router.callback_query(F.data == "editprofile:name")
async def editprofile_name(call: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.name)
    await call.message.answer("Yangi ismingizni yozing:")
    await call.answer()

@router.callback_query(F.data == "editprofile:phone")
async def editprofile_phone(call: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.phone)
    await call.message.answer("Yangi telefon raqamingizni yuboring:", reply_markup=kb_phone())
    await call.answer()

@router.message(EditProfile.name)
async def save_new_name(message: Message, state: FSMContext):
    await db.execute("UPDATE users SET name=? WHERE tg_id=?", (message.text.strip(), message.from_user.id))
    await db.commit()
    await state.clear()
    await message.answer("✅ Ism yangilandi.", reply_markup=await kb_main(message.from_user.id))

@router.message(EditProfile.phone, F.contact)
async def save_new_phone_c(message: Message, state: FSMContext):
    await _save_new_phone(message, state, message.contact.phone_number)

@router.message(EditProfile.phone, F.text)
async def save_new_phone_t(message: Message, state: FSMContext):
    await _save_new_phone(message, state, message.text.strip())

async def _save_new_phone(message, state, phone):
    await db.execute("UPDATE users SET phone=? WHERE tg_id=?", (phone, message.from_user.id))
    await db.commit()
    await state.clear()
    await message.answer("✅ Telefon raqam yangilandi.", reply_markup=await kb_main(message.from_user.id))

# ============================== CATALOG ======================================

@router.message(F.text == "🗂 Katalog")
async def catalog_open(message: Message):
    cur = await db.execute("SELECT * FROM categories ORDER BY id")
    cats = await cur.fetchall()
    if not cats:
        await message.answer("Hozircha bo'limlar mavjud emas.")
        return
    cur2 = await db.execute("SELECT COUNT(*) c FROM products WHERE is_top=1")
    top_count = (await cur2.fetchone())["c"]
    rows = []
    if top_count:
        rows.append([("🔥 Top mebellar", "topcat")])
    for c in cats:
        rows.append([(c["name"], f"cat:{c['id']}")])
    await message.answer("🗂 Bo'limni tanlang:", reply_markup=ikb(rows))

@router.callback_query(F.data == "topcat")
async def show_top(call: CallbackQuery):
    cur = await db.execute("SELECT id FROM products WHERE is_top=1")
    rows = await cur.fetchall()
    await _show_product_grid(call, [r["id"] for r in rows], "🔥 Top mebellar")

@router.callback_query(F.data.startswith("cat:"))
async def show_category(call: CallbackQuery):
    cat_id = int(call.data.split(":")[1])
    cur = await db.execute("SELECT id FROM products WHERE category_id=?", (cat_id,))
    rows = await cur.fetchall()
    cat = await db.execute("SELECT name FROM categories WHERE id=?", (cat_id,))
    cat_row = await cat.fetchone()
    await _show_product_grid(call, [r["id"] for r in rows], cat_row["name"] if cat_row else "Bo'lim")

async def _show_product_grid(call: CallbackQuery, product_ids, title):
    if not product_ids:
        await call.message.answer("Bu bo'limda hozircha mebel yo'q.")
        await call.answer()
        return

    await call.message.answer(f"📦 <b>{title}</b> bo'limidagi mebellar:")

    cur = await db.execute(
        f"SELECT * FROM products WHERE id IN ({','.join('?' * len(product_ids))})",
        product_ids,
    )
    prods = await cur.fetchall()

    for p in prods:
        photos = json.loads(p["photos"] or "[]")
        caption = (
            f"<b>{p['name']}</b> ({p['sku']})\n"
            f"💰 Narxi: <b>{fmt_price(p['price'])}</b>\n"
            f"{'✅ Omborda bor' if p['quantity'] and p['quantity'] > 0 else '⚠️ Buyurtma bo\'yicha yasab beriladi'}"
        )
        kb = ikb([
            [("👁 Batafsil ko'rish", f"prod:{p['id']}"), ("🛒 Savatga", f"addcart:{p['id']}")]
        ])
        if photos:
            await bot.send_photo(call.message.chat.id, photos[0], caption=caption, reply_markup=kb)
        else:
            await bot.send_message(call.message.chat.id, caption, reply_markup=kb)
    await call.answer()

async def _product_caption(p, idx, total_photos) -> str:
    lines = [f"<b>{p['name']}</b>  (Kod: {p['sku']})"]
    if p["description"]:
        lines.append(p["description"])
    if p["old_price"]:
        lines.append(f"<s>{fmt_price(p['old_price'])}</s>  <b>{fmt_price(p['price'])}</b>")
    else:
        lines.append(f"Narxi: <b>{fmt_price(p['price'])}</b>")
    if p["quantity"] and p["quantity"] > 0:
        lines.append(f"✅ Omborda bor ({p['quantity']} dona)")
    else:
        lines.append("⚠️ Omborda qolmagan. Buyurtma bersangiz, xuddi shunday yasab beramiz.")
    if total_photos > 1:
        lines.append(f"\n🖼 {idx + 1}/{total_photos}")
    return "\n".join(lines)

async def _product_kb(p, user_id, idx):
    fav_cur = await db.execute(
        "SELECT 1 FROM favorites WHERE user_id=? AND product_id=?", (user_id, p["id"])
    )
    is_fav = await fav_cur.fetchone() is not None
    photos = json.loads(p["photos"] or "[]")
    nav = []
    if len(photos) > 1:
        nav = [
            ("⬅️", f"prodimg:{p['id']}:{(idx - 1) % len(photos)}"),
            ("➡️", f"prodimg:{p['id']}:{(idx + 1) % len(photos)}"),
        ]
    rows = []
    if nav:
        rows.append(nav)
    rows.append([
        ("💔 Tanlanganlardan olib tashlash" if is_fav else "❤️ Tanlanganlarga qo'shish", f"fav:{p['id']}"),
    ])
    if p["quantity"] and p["quantity"] > 0:
        rows.append([("🛒 Savatga qo'shish", f"addcart:{p['id']}")])
        rows.append([("✅ Sotib olish", f"buy:{p['id']}")])
    else:
        rows.append([("🔨 Buyurtma berish (yasab beramiz)", f"buy:{p['id']}")])
        rows.append([("🔔 Kelganda xabar bering", f"notifyme:{p['id']}")])
    rows.append([("↩️ Ulashish", f"share:{p['id']}")])
    return ikb(rows)

async def show_product(chat_id, user_id, product_id, idx):
    cur = await db.execute("SELECT * FROM products WHERE id=?", (product_id,))
    p = await cur.fetchone()
    if not p:
        await bot.send_message(chat_id, "Mahsulot topilmadi.")
        return
    photos = json.loads(p["photos"] or "[]")
    caption = await _product_caption(p, idx, len(photos))
    kb = await _product_kb(p, user_id, idx)
    if photos:
        await bot.send_photo(chat_id, photos[idx], caption=caption, reply_markup=kb)
    else:
        await bot.send_message(chat_id, caption, reply_markup=kb)

@router.callback_query(F.data.startswith("prod:"))
async def cb_show_product(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    await show_product(call.message.chat.id, call.from_user.id, pid, 0)
    await call.answer()

@router.callback_query(F.data.startswith("prodimg:"))
async def cb_prod_img(call: CallbackQuery):
    _, pid, idx = call.data.split(":")
    pid, idx = int(pid), int(idx)
    cur = await db.execute("SELECT * FROM products WHERE id=?", (pid,))
    p = await cur.fetchone()
    photos = json.loads(p["photos"] or "[]")
    caption = await _product_caption(p, idx, len(photos))
    kb = await _product_kb(p, call.from_user.id, idx)
    try:
        await call.message.edit_media(InputMediaPhoto(media=photos[idx], caption=caption), reply_markup=kb)
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data.startswith("fav:"))
async def cb_toggle_fav(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    cur = await db.execute(
        "SELECT id FROM favorites WHERE user_id=? AND product_id=?", (call.from_user.id, pid)
    )
    row = await cur.fetchone()
    if row:
        await db.execute("DELETE FROM favorites WHERE id=?", (row["id"],))
        await call.answer("Tanlanganlardan olib tashlandi")
    else:
        await db.execute(
            "INSERT OR IGNORE INTO favorites(user_id, product_id) VALUES (?,?)", (call.from_user.id, pid)
        )
        await call.answer("❤️ Tanlanganlarga qo'shildi")
    await db.commit()
    p = await (await db.execute("SELECT * FROM products WHERE id=?", (pid,))).fetchone()
    kb = await _product_kb(p, call.from_user.id, 0)
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data.startswith("share:"))
async def cb_share(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=product_{pid}"
    await call.message.answer(f"🔗 Ulashish uchun havola:\n{link}")
    await call.answer()

@router.callback_query(F.data.startswith("notifyme:"))
async def cb_notify_me(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    await db.execute(
        "INSERT OR IGNORE INTO notify_stock(user_id, product_id) VALUES (?,?)", (call.from_user.id, pid)
    )
    await db.commit()
    await call.answer("✅ Mebel omborga kelganda sizga xabar beramiz.", show_alert=True)

@router.message(F.text == "❤️ Tanlanganlar")
async def show_favorites(message: Message):
    cur = await db.execute("SELECT product_id FROM favorites WHERE user_id=?", (message.from_user.id,))
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Tanlanganlar ro'yxati bo'sh.")
        return
    for r in rows:
        p = await (await db.execute("SELECT * FROM products WHERE id=?", (r["product_id"],))).fetchone()
        if p:
            await show_product(message.chat.id, message.from_user.id, p["id"], 0)

# ============================== CART =========================================

@router.callback_query(F.data.startswith("addcart:"))
async def cb_add_cart(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    cur = await db.execute("SELECT * FROM cart WHERE user_id=? AND product_id=?", (call.from_user.id, pid))
    row = await cur.fetchone()
    if row:
        await db.execute("UPDATE cart SET qty=qty+1 WHERE id=?", (row["id"],))
    else:
        await db.execute("INSERT INTO cart(user_id, product_id, qty) VALUES (?,?,1)", (call.from_user.id, pid))
    await db.commit()
    await call.answer("🛒 Savatga qo'shildi")

@router.message(F.text == "🛒 Savat")
async def show_cart(message: Message):
    await _render_cart(message.chat.id, message.from_user.id)

async def _render_cart(chat_id, user_id):
    cur = await db.execute("SELECT * FROM cart WHERE user_id=?", (user_id,))
    items = await cur.fetchall()
    if not items:
        await bot.send_message(chat_id, "🛒 Savatingiz bo'sh.")
        return
    text = "🛒 <b>Savatingiz:</b>\n\n"
    total = 0
    rows = []
    for it in items:
        p = await (await db.execute("SELECT * FROM products WHERE id=?", (it["product_id"],))).fetchone()
        if not p:
            continue
        line_total = (p["price"] or 0) * it["qty"]
        total += line_total
        text += f"• {p['name']} x{it['qty']} — {fmt_price(p['price'])}\n"
        rows.append([("❌ " + p["name"][:20], f"cartdel:{it['id']}")])
    text += f"\n<b>Jami: {fmt_price(total)}</b>"
    rows.append([("🗑 Savatni tozalash", "cartclear")])
    rows.append([("✅ Buyurtma berish", "checkout")])
    await bot.send_message(chat_id, text, reply_markup=ikb(rows))

@router.callback_query(F.data.startswith("cartdel:"))
async def cb_cart_del(call: CallbackQuery):
    cid = int(call.data.split(":")[1])
    await db.execute("DELETE FROM cart WHERE id=?", (cid,))
    await db.commit()
    await call.answer("O'chirildi")
    await _render_cart(call.message.chat.id, call.from_user.id)

@router.callback_query(F.data == "cartclear")
async def cb_cart_clear(call: CallbackQuery):
    await db.execute("DELETE FROM cart WHERE user_id=?", (call.from_user.id,))
    await db.commit()
    await call.answer("Savat tozalandi")
    await call.message.answer("🗑 Savat bo'shatildi.")

# ============================== ORDER FLOW ===================================

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_single(call: CallbackQuery, state: FSMContext):
    pid = int(call.data.split(":")[1])
    await state.update_data(mode="single", product_id=pid)
    await state.set_state(OrderFlow.location)
    await call.message.answer(
        "Buyurtmani rasmiylashtirish uchun lokatsiyangizni yuboring:",
        reply_markup=kb_location(),
    )
    await call.answer()

@router.callback_query(F.data == "checkout")
async def cb_checkout(call: CallbackQuery, state: FSMContext):
    cur = await db.execute("SELECT COUNT(*) c FROM cart WHERE user_id=?", (call.from_user.id,))
    if (await cur.fetchone())["c"] == 0:
        await call.answer("Savat bo'sh", show_alert=True)
        return
    await state.update_data(mode="cart")
    await state.set_state(OrderFlow.location)
    await call.message.answer(
        "Buyurtmani rasmiylashtirish uchun lokatsiyangizni yuboring:",
        reply_markup=kb_location(),
    )
    await call.answer()

@router.message(OrderFlow.location, F.text == "⛔️ Bekor qilish")
async def order_cancel_flow(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=await kb_main(message.from_user.id))

@router.message(OrderFlow.location, F.location)
async def order_got_location(message: Message, state: FSMContext):
    await state.update_data(lat=message.location.latitude, lon=message.location.longitude)
    await state.set_state(OrderFlow.comment)
    await message.answer(
        "O'lchamlaringiz yoki qo'shimcha xohishingiz bo'lsa yozing. "
        "Agar bo'lmasa /skip deb yozing.",
        reply_markup=ReplyKeyboardRemove(),
    )

@router.message(OrderFlow.comment)
async def order_got_comment(message: Message, state: FSMContext):
    comment = None if message.text.strip() == "/skip" else message.text.strip()
    data = await state.get_data()
    lat, lon = data["lat"], data["lon"]

    w_lat = float(await get_setting("workshop_lat"))
    w_lon = float(await get_setting("workshop_lon"))
    dist = haversine_km(lat, lon, w_lat, w_lon)

    items = []
    if data.get("mode") == "single":
        p = await (await db.execute("SELECT * FROM products WHERE id=?", (data["product_id"],))).fetchone()
        items.extend(await _split_stock(p, 1))
    else:
        cart_rows = await (await db.execute("SELECT * FROM cart WHERE user_id=?", (message.from_user.id,))).fetchall()
        for cr in cart_rows:
            p = await (await db.execute("SELECT * FROM products WHERE id=?", (cr["product_id"],))).fetchone()
            if p:
                items.extend(await _split_stock(p, cr["qty"]))
        await db.execute("DELETE FROM cart WHERE user_id=?", (message.from_user.id,))

    total = sum((it[3] or 0) * it[2] for it in items)
    code = await gen_order_code()
    now = datetime.now().isoformat()
    cur = await db.execute(
        "INSERT INTO orders(code, user_id, total, status, comment, lat, lon, distance_km, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (code, message.from_user.id, total, "yangi", comment, lat, lon, dist, now),
    )
    order_id = cur.lastrowid
    for pid, name, qty, price, itype in items:
        await db.execute(
            "INSERT INTO order_items(order_id, product_id, name, qty, price, item_type) VALUES (?,?,?,?,?,?)",
            (order_id, pid, name, qty, price, itype),
        )
    await db.commit()
    await state.clear()

    closed_note = ""
    if (await get_setting("is_open")) == "0":
        closed_note = "\n\n🌙 Hozir ustaxonamiz yopiq, ish vaqti boshlanishi bilan siz bilan bog'lanamiz."

    await message.answer(
        f"✅ Buyurtmangiz qabul qilindi! Buyurtma raqami: <b>{code}</b>{closed_note}",
        reply_markup=await kb_main(message.from_user.id),
    )
    await _notify_workers_new_order(order_id)

async def _split_stock(p, qty):
    result = []
    if p["quantity"] and p["quantity"] > 0:
        avail = min(p["quantity"], qty)
        result.append((p["id"], p["name"], avail, p["price"], "stock"))
        await db.execute("UPDATE products SET quantity=quantity-? WHERE id=?", (avail, p["id"]))
        remainder = qty - avail
        if remainder > 0:
            result.append((p["id"], p["name"], remainder, p["price"], "custom"))
    else:
        result.append((p["id"], p["name"], qty, p["price"], "custom"))
    return result

async def _notify_workers_new_order(order_id):
    o = await (await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))).fetchone()
    user = await get_user(o["user_id"])
    items = await (await db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,))).fetchall()
    lines = [f"🆕 <b>Yangi buyurtma {o['code']}</b>",
             f"Mijoz: {user['name']} ({user['phone']})",
             f"Masofa: {fmt_distance(o['distance_km'])}"]
    for it in items:
        kind = "ombordan" if it["item_type"] == "stock" else "yasalishi kerak"
        lines.append(f"• {it['name']} x{it['qty']} ({kind}) — {fmt_price(it['price'])}")
    if o["comment"]:
        lines.append(f"Izoh: {o['comment']}")
    lines.append(f"Jami: {fmt_price(o['total'])}")
    text = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Xaritada ochish", url=maps_link(o["lat"], o["lon"]))],
        [InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"take:{order_id}")],
    ])
    if WORKER_GROUP_ID:
        m = await bot.send_message(WORKER_GROUP_ID, text, reply_markup=kb)
        await db.execute(
            "INSERT INTO msg_map(admin_chat_id, admin_msg_id, customer_id, created_at) VALUES (?,?,?,?)",
            (WORKER_GROUP_ID, m.message_id, o["user_id"], datetime.now().isoformat()),
        )
    for admin_id in ADMIN_IDS:
        try:
            m = await bot.send_message(admin_id, text, reply_markup=kb)
            await db.execute(
                "INSERT INTO msg_map(admin_chat_id, admin_msg_id, customer_id, created_at) VALUES (?,?,?,?)",
                (admin_id, m.message_id, o["user_id"], datetime.now().isoformat()),
            )
        except Exception:
            pass
    await db.commit()

@router.callback_query(F.data.startswith("take:"))
async def cb_worker_take(call: CallbackQuery):
    if not await is_worker(call.from_user.id):
        await call.answer("Sizda ruxsat yo'q", show_alert=True)
        return
    order_id = int(call.data.split(":")[1])
    o = await (await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))).fetchone()
    if o["worker_id"]:
        await call.answer("Bu buyurtmani allaqachon boshqa usta qabul qilgan.", show_alert=True)
        return
    await db.execute(
        "UPDATE orders SET worker_id=?, status='qabul_qilindi' WHERE id=?", (call.from_user.id, order_id)
    )
    await db.commit()
    try:
        await call.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗺 Xaritada ochish", url=maps_link(o["lat"], o["lon"]))],
                [InlineKeyboardButton(text=f"✅ Qabul qildi: {call.from_user.full_name}", callback_data="noop")],
            ])
        )
    except Exception:
        pass
    await bot.send_message(
        call.from_user.id,
        f"Siz {o['code']} buyurtmasini qabul qildingiz.",
        reply_markup=ikb([
            [("🔄 Jarayonda", f"ordstatus:{order_id}:jarayonda")],
            [("✅ Tayyor", f"ordstatus:{order_id}:tayyor")],
            [("🏁 Yopish", f"ordstatus:{order_id}:yopildi")],
        ]),
    )
    try:
        await bot.send_message(o["user_id"], f"Sizning {o['code']} buyurtmangiz qabul qilindi, usta ishga tushdi.")
    except Exception:
        pass
    await call.answer("Qabul qilindi ✅")

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()

@router.callback_query(F.data.startswith("ordstatus:"))
async def cb_order_status(call: CallbackQuery):
    _, order_id, status = call.data.split(":")
    order_id = int(order_id)
    await db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    await db.commit()
    o = await (await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))).fetchone()
    labels = {"jarayonda": "🔄 Jarayonda", "tayyor": "✅ Tayyor bo'ldi", "yopildi": "🏁 Yakunlandi"}
    try:
        await bot.send_message(o["user_id"], f"Buyurtmangiz {o['code']} holati: {labels.get(status, status)}")
    except Exception:
        pass
    await call.answer("Holat yangilandi")

# ============================== MY ORDERS ====================================

@router.message(F.text == "📦 Buyurtmalarim")
async def my_orders(message: Message):
    cur = await db.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20", (message.from_user.id,)
    )
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Sizda hali buyurtmalar yo'q.")
        return
    labels = {
        "yangi": "🆕 Yangi", "qabul_qilindi": "👨‍🔧 Usta qabul qildi",
        "jarayonda": "🔄 Jarayonda", "tayyor": "✅ Tayyor",
        "yopildi": "🏁 Yakunlandi", "bekor_qilindi": "❌ Bekor qilindi",
    }
    for o in rows:
        text = f"{o['code']} — {labels.get(o['status'], o['status'])}\nJami: {fmt_price(o['total'])}"
        kb = None
        if o["status"] in ("yangi",):
            kb = ikb([[("❌ Bekor qilish", f"ordcancel:{o['id']}")]])
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("ordcancel:"))
async def cb_order_cancel(call: CallbackQuery):
    order_id = int(call.data.split(":")[1])
    o = await (await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))).fetchone()
    if not o or o["user_id"] != call.from_user.id or o["status"] != "yangi":
        await call.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        return
    await db.execute("UPDATE orders SET status='bekor_qilindi' WHERE id=?", (order_id,))
    await db.commit()
    await call.answer("Buyurtma bekor qilindi")
    await call.message.edit_text(f"{o['code']} — ❌ Bekor qilindi")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⚠️ Mijoz {o['code']} buyurtmasini bekor qildi.")
        except Exception:
            pass

# ============================== CUSTOM ORDER (own project) ===================

@router.message(F.text == "📐 O'z loyihamni yuborish")
async def custom_order_start(message: Message, state: FSMContext):
    await state.set_state(CustomOrder.photo)
    await message.answer("O'zingiz xohlagan mebel rasmini yuboring:")

@router.message(CustomOrder.photo, F.photo)
async def custom_order_photo(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await state.set_state(CustomOrder.desc)
    await message.answer("Endi taxminiy o'lcham va tavsifni yozing:")

@router.message(CustomOrder.desc)
async def custom_order_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    user = await get_user(message.from_user.id)
    caption = (
        f"📐 <b>Yangi maxsus loyiha so'rovi</b>\n"
        f"Mijoz: {user['name']} ({user['phone']})\n"
        f"Tavsif: {message.text.strip()}\nNarxi: kelishiladi"
    )
    for admin_id in ADMIN_IDS:
        try:
            m = await bot.send_photo(admin_id, data["photo"], caption=caption)
            await db.execute(
                "INSERT INTO msg_map(admin_chat_id, admin_msg_id, customer_id, created_at) VALUES (?,?,?,?)",
                (admin_id, m.message_id, message.from_user.id, datetime.now().isoformat()),
            )
        except Exception:
            pass
    await db.commit()
    await state.clear()
    await message.answer(
        "✅ So'rovingiz adminga yuborildi, tez orada siz bilan bog'lanamiz.",
        reply_markup=await kb_main(message.from_user.id),
    )

# ============================== ADDRESS / HELP / CALL / PORTFOLIO ============

@router.message(F.text == "🖼 Portfolio")
async def show_user_portfolio(message: Message):
    cur = await db.execute("SELECT * FROM portfolio ORDER BY id DESC LIMIT 10")
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Hozircha portfolioda rasmlar yo'q.")
        return
    await message.answer("🖼 <b>Bizning bajargan ishlarimizdan namunalar:</b>")
    for item in rows:
        try:
            await message.answer_photo(
                photo=item["photo_file_id"],
                caption=f"✨ {item['caption']}"
            )
        except Exception:
            pass

@router.message(F.text == "📍 Bizning manzil")
async def show_address(message: Message):
    lat = float(await get_setting("workshop_lat"))
    lon = float(await get_setting("workshop_lon"))
    phone = await get_setting("workshop_phone")
    await message.answer(f"☎️ Telefon: {phone}")
    await message.answer_location(lat, lon)

@router.message(F.text == "🆘 Yordam")
async def show_help(message: Message):
    text = await get_setting("help_text")
    await message.answer(f"🆘 {text}")

@router.message(F.text == "☎️ Qo'ng'iroq so'rash")
async def call_request(message: Message):
    user = await get_user(message.from_user.id)
    closed_note = ""
    if (await get_setting("is_open")) == "0":
        closed_note = "\n(Hozir ish vaqti tugagan, ertaga bog'lanamiz)"
    for admin_id in ADMIN_IDS:
        try:
            m = await bot.send_message(
                admin_id,
                f"📞 Mijoz {user['name']} ({user['phone']}) sizdan qo'ng'iroq kutyapti.{closed_note}",
            )
            await db.execute(
                "INSERT INTO msg_map(admin_chat_id, admin_msg_id, customer_id, created_at) VALUES (?,?,?,?)",
                (admin_id, m.message_id, message.from_user.id, datetime.now().isoformat()),
            )
        except Exception:
            pass
    await db.commit()
    await message.answer("✅ So'rovingiz yuborildi, tez orada sizga qo'ng'iroq qilamiz.")

# ============================== CRM: REPLY ===================================

@router.message(F.reply_to_message)
async def crm_reply_handler(message: Message):
    if not (await is_admin(message.from_user.id) or await is_worker(message.from_user.id)):
        return
    cur = await db.execute(
        "SELECT customer_id FROM msg_map WHERE admin_chat_id=? AND admin_msg_id=?",
        (message.chat.id, message.reply_to_message.message_id),
    )
    row = await cur.fetchone()
    if not row:
        return
    try:
        await bot.send_message(row["customer_id"], f"💬 Admin javobi:\n{message.text or message.caption or ''}")
        await message.reply("✅ Mijozga yuborildi.")
    except Exception:
        await message.reply("❌ Mijozga yuborib bo'lmadi (u botni bloklagan bo'lishi mumkin).")

# ============================== WORKER PANEL =================================

@router.message(F.text == "🛠 Usta paneli")
async def worker_panel(message: Message):
    if not await is_worker(message.from_user.id):
        return
    cur = await db.execute(
        "SELECT * FROM orders WHERE worker_id=? AND status IN ('qabul_qilindi','jarayonda') ORDER BY id DESC",
        (message.from_user.id,),
    )
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Sizda faol buyurtmalar yo'q.")
        return
    for o in rows:
        await message.answer(
            f"{o['code']} — {o['status']}",
            reply_markup=ikb([
                [("🔄 Jarayonda", f"ordstatus:{o['id']}:jarayonda")],
                [("✅ Tayyor", f"ordstatus:{o['id']}:tayyor")],
                [("🏁 Yopish", f"ordstatus:{o['id']}:yopildi")],
            ]),
        )

# ============================== ADMIN PANEL ==================================

@router.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await message.answer(
        "⚙️ <b>Admin panelga xush kelibsiz!</b>\nKerakli bo'limni tanlang:",
        reply_markup=kb_admin()
    )

# ---------- Clients Management ----------

@router.message(F.text == "👥 Mijozlar bo'limi")
async def adm_clients_menu(message: Message):
    if not await is_admin(message.from_user.id):
        return
    cur = await db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 20")
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Hozircha foydalanuvchilar yo'q.")
        return
    await message.answer("👥 <b>Mijozlar va foydalanuvchilar ro'yxati:</b>")
    for u in rows:
        await _send_user_card(message.chat.id, u)

async def _send_user_card(chat_id, u):
    cnt = await (await db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders WHERE user_id=?", (u["tg_id"],)
    )).fetchone()
    role_text = "👑 Admin" if u["role"] == "admin" else ("🛠 Usta" if u["role"] == "worker" else "👤 Oddiy mijoz")
    text = (
        f"👤 <b>Ismi:</b> {u['name']}\n"
        f"📞 <b>Raqami:</b> {u['phone']}\n"
        f"🆔 <b>ID:</b> <code>{u['tg_id']}</code>\n"
        f"🎭 <b>Lavozimi:</b> {role_text}\n"
        f"🚫 <b>Holati:</b> {'Bloklangan' if u['banned'] else 'Faol'}\n"
        f"📦 <b>Buyurtmalari:</b> {cnt['n']} ta ({fmt_price(cnt['s'])})"
    )
    rows = []
    if u["role"] != "worker":
        rows.append([("🛠 Usta qilish", f"adm_user_role:{u['tg_id']}:worker")])
    if u["role"] != "admin":
        rows.append([("👑 Admin qilish", f"adm_user_role:{u['tg_id']}:admin")])
    if u["role"] != "user":
        rows.append([("👤 Oddiy foydalanuvchi qilish", f"adm_user_role:{u['tg_id']}:user")])
    if u["banned"]:
        rows.append([("✅ Blokdan chiqarish", f"adm_user_ban:{u['tg_id']}:0")])
    else:
        rows.append([("🚫 Bloklash", f"adm_user_ban:{u['tg_id']}:1")])
    rows.append([("🗑 Foydalanuvchini o'chirish", f"adm_user_del:{u['tg_id']}")])
    await bot.send_message(chat_id, text, reply_markup=ikb(rows))


@router.message(F.text == "👨‍💼 Xodimlar")
async def adm_staff_menu(message: Message):
    if not await is_admin(message.from_user.id):
        return
    
    cur = await db.execute("SELECT * FROM users WHERE role IN ('admin', 'worker')")
    staff = await cur.fetchall()
    
    admin_text = ""
    worker_text = ""
    for u in staff:
        if u['role'] == 'admin':
            admin_text += f"▫️ {u['name']} | 🆔 <code>{u['tg_id']}</code>\n"
        else:
            worker_text += f"▫️ {u['name']} | 🆔 <code>{u['tg_id']}</code>\n"
            
    text = (
        "<b>👨‍💼 Xodimlar bo'limi</b>\n\n"
        "<i>Joriy xodimlar ro'yxati:</i>\n\n"
        "👑 <b>Adminlar:</b>\n"
        f"{admin_text if admin_text else 'Yoq'}\n"
        "🛠 <b>Ustalar:</b>\n"
        f"{worker_text if worker_text else 'Yoq'}\n"
        "👇 <i>Yangi xodim qo'shish uchun uning **Telegram ID** raqamini botga yuboring "
        "(yoki Mijozlar bo'limidan foydalaning):</i>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

# XATOLIK MANBAI SHU YERDA EDI: StateFilter(None) qo'shildi!
@router.message(StateFilter(None), F.text.regexp(r"^\d+$"))
async def adm_set_role_by_id(message: Message):
    if not await is_admin(message.from_user.id):
        return
    target_id = int(message.text)
    user = await get_user(target_id)
    if not user:
        await message.answer(f"❌ ID: <code>{target_id}</code> bo'lgan foydalanuvchi bazadan topilmadi. U avval botga /start bosgan bo'lishi kerak.")
        return
    role_text = "👑 Admin" if user["role"] == "admin" else ("🛠 Usta" if user["role"] == "worker" else "👤 Oddiy mijoz")
    text = (
        f"👤 <b>Topilgan foydalanuvchi:</b>\n"
        f"Ismi: {user['name']}\n"
        f"Raqami: {user['phone']}\n"
        f"ID: <code>{user['tg_id']}</code>\n"
        f"Hozirgi roli: {role_text}\n\n"
        f"Ushbu foydalanuvchiga qaysi lavozimni bermoqchisiz?"
    )
    await message.answer(
        text,
        reply_markup=ikb([
            [("👑 Admin qilish", f"adm_user_role:{target_id}:admin"), ("🛠 Usta qilish", f"adm_user_role:{target_id}:worker")],
            [("👤 Oddiy mijoz qilish", f"adm_user_role:{target_id}:user")]
        ])
    )


@router.callback_query(F.data.startswith("adm_user_role:"))
async def adm_user_role(call: CallbackQuery):
    _, tg_id, role = call.data.split(":")
    tg_id = int(tg_id)
    user = await get_user(tg_id)
    await db.execute("UPDATE users SET role=? WHERE tg_id=?", (role, tg_id))
    await db.commit()
    role_titles = {"admin": "Admin 👑", "worker": "Usta 🛠", "user": "Foydalanuvchi 👤"}
    title = role_titles.get(role, role)
    await call.answer(f"Lavozim: {title}")
    await call.message.answer(f"✅ Foydalanuvchi ({user['name']}) lavozimi '{title}' qilib belgilandi.")
    try:
        await bot.send_message(tg_id, f"🎉 Siz <b>{title}</b> qilindingiz!\nIsmingiz: <b>{user['name']}</b>", reply_markup=await kb_main(tg_id))
    except Exception:
        pass

@router.callback_query(F.data.startswith("adm_user_ban:"))
async def adm_user_ban(call: CallbackQuery):
    _, tg_id, val = call.data.split(":")
    await db.execute("UPDATE users SET banned=? WHERE tg_id=?", (int(val), int(tg_id)))
    await db.commit()
    await call.answer("Bajarildi")
    await call.message.answer("✅ Foydalanuvchi holati yangilandi.")

@router.callback_query(F.data.startswith("adm_user_del:"))
async def adm_user_del(call: CallbackQuery):
    tg_id = int(call.data.split(":")[1])
    await db.execute("DELETE FROM users WHERE tg_id=?", (tg_id,))
    await db.commit()
    await call.answer("Foydalanuvchi o'chirildi", show_alert=True)
    await call.message.delete()

# ---------- Categories Management ----------

@router.message(F.text == "🗂 Bo'limlar boshqaruvi")
async def adm_cats(message: Message):
    if not await is_admin(message.from_user.id):
        return
    cur = await db.execute("SELECT * FROM categories ORDER BY id")
    cats = await cur.fetchall()
    rows = []
    for c in cats:
        cnt = await (await db.execute("SELECT COUNT(*) n FROM products WHERE category_id=?", (c["id"],))).fetchone()
        rows.append([(f"{c['name']} ({cnt['n']})", f"adm_cat_open:{c['id']}")])
    rows.append([("➕ Yangi bo'lim qo'shish", "adm_cat_add")])
    await message.answer("🗂 Bo'limlar boshqaruvi:", reply_markup=ikb(rows))

@router.callback_query(F.data == "adm_cat_add")
async def adm_cat_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminCat.add_name)
    await call.message.answer("Yangi bo'lim nomini yozing:")
    await call.answer()

@router.message(AdminCat.add_name)
async def adm_cat_add_save(message: Message, state: FSMContext):
    await db.execute("INSERT INTO categories(name) VALUES (?)", (message.text.strip(),))
    await db.commit()
    await state.clear()
    await message.answer("✅ Bo'lim qo'shildi.", reply_markup=kb_admin())

@router.callback_query(F.data.startswith("adm_cat_open:"))
async def adm_cat_open(call: CallbackQuery):
    cat_id = int(call.data.split(":")[1])
    cur = await db.execute("SELECT * FROM products WHERE category_id=?", (cat_id,))
    prods = await cur.fetchall()
    rows = [[(p["name"], f"adm_prod_edit:{p['id']}")] for p in prods]
    rows.append([("➕ Mahsulot qo'shish", f"adm_prod_add:{cat_id}")])
    rows.append([("✏️ Nomini o'zgartirish", f"adm_cat_rename:{cat_id}")])
    rows.append([("🗑 Bo'limni o'chirish", f"adm_cat_del:{cat_id}")])
    await call.message.answer("Bo'lim boshqaruvi:", reply_markup=ikb(rows))
    await call.answer()

@router.callback_query(F.data.startswith("adm_cat_rename:"))
async def adm_cat_rename(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split(":")[1])
    await state.update_data(cat_id=cat_id)
    await state.set_state(AdminCat.edit_name)
    await call.message.answer("Yangi nomni yozing:")
    await call.answer()

@router.message(AdminCat.edit_name)
async def adm_cat_rename_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.execute("UPDATE categories SET name=? WHERE id=?", (message.text.strip(), data["cat_id"]))
    await db.commit()
    await state.clear()
    await message.answer("✅ Yangilandi.", reply_markup=kb_admin())

@router.callback_query(F.data.startswith("adm_cat_del:"))
async def adm_cat_del(call: CallbackQuery):
    cat_id = int(call.data.split(":")[1])
    cnt = await (await db.execute("SELECT COUNT(*) n FROM products WHERE category_id=?", (cat_id,))).fetchone()
    if cnt["n"] > 0:
        await call.message.answer(
            f"⚠️ Bu bo'limda {cnt['n']} ta mebel bor. Ularni ham o'chirishni tasdiqlaysizmi?",
            reply_markup=ikb([[("✅ Ha, o'chirish", f"adm_cat_del_confirm:{cat_id}")],
                               [("⛔️ Bekor qilish", "noop")]]),
        )
    else:
        await db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        await db.commit()
        await call.message.answer("✅ Bo'lim o'chirildi.")
    await call.answer()

@router.callback_query(F.data.startswith("adm_cat_del_confirm:"))
async def adm_cat_del_confirm(call: CallbackQuery):
    cat_id = int(call.data.split(":")[1])
    await db.execute("DELETE FROM products WHERE category_id=?", (cat_id,))
    await db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    await db.commit()
    await call.message.answer("✅ Bo'lim va uning mahsulotlari o'chirildi.")
    await call.answer()

# ---------- Products Management ----------

@router.callback_query(F.data.startswith("adm_prod_add:"))
async def adm_prod_add(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split(":")[1])
    await state.update_data(category_id=cat_id, photos=[])
    await state.set_state(AdminProd.name)
    await call.message.answer("Mahsulot nomini yozing:")
    await call.answer()

@router.message(AdminProd.name)
async def adm_prod_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminProd.desc)
    await message.answer("Tavsifini yozing (yoki /skip):")

@router.message(AdminProd.desc)
async def adm_prod_desc(message: Message, state: FSMContext):
    desc = None if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(AdminProd.qty)
    await message.answer("Ombordagi sonini yozing (masalan 0 yoki 5):")

@router.message(AdminProd.qty)
async def adm_prod_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
    except ValueError:
        await message.answer("Iltimos, butun son kiriting.")
        return
    await state.update_data(quantity=qty)
    await state.set_state(AdminProd.price)
    await message.answer(
        "Narx turini tanlang:",
        reply_markup=ikb([[("💰 Aniq narx", "ptype:exact")], [("🤝 Narxni kelishamiz", "ptype:negotiate")]]),
    )

@router.callback_query(F.data == "ptype:negotiate", AdminProd.price)
async def adm_prod_price_negotiate(call: CallbackQuery, state: FSMContext):
    await state.update_data(price=None, old_price=None)
    await state.set_state(AdminProd.photos)
    await call.message.answer("Endi mahsulot rasm(lar)ini yuboring. Tugagach /done deb yozing.")
    await call.answer()

@router.callback_query(F.data == "ptype:exact", AdminProd.price)
async def adm_prod_price_exact(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Narxni kiriting (so'mda, faqat raqam):")
    await call.answer()

@router.message(AdminProd.price)
async def adm_prod_price_value(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer("Iltimos, narxni faqat raqam bilan kiriting.")
        return
    await state.update_data(price=price)
    await state.set_state(AdminProd.new_price)
    await message.answer(
        "Chegirma bormi?",
        reply_markup=ikb([[("Ha", "discount:yes")], [("Yo'q", "discount:no")]]),
    )

@router.callback_query(F.data == "discount:no", AdminProd.new_price)
async def adm_prod_discount_no(call: CallbackQuery, state: FSMContext):
    await state.update_data(old_price=None)
    await state.set_state(AdminProd.photos)
    await call.message.answer("Endi mahsulot rasm(lar)ini yuboring. Tugagach /done deb yozing.")
    await call.answer()

@router.callback_query(F.data == "discount:yes", AdminProd.new_price)
async def adm_prod_discount_yes(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Yangi (chegirmali) narxni kiriting:")
    await call.answer()

@router.message(AdminProd.new_price)
async def adm_prod_new_price_value(message: Message, state: FSMContext):
    try:
        new_price = float(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer("Iltimos, faqat raqam kiriting.")
        return
    data = await state.get_data()
    await state.update_data(old_price=data["price"], price=new_price)
    await state.set_state(AdminProd.photos)
    await message.answer("Endi mahsulot rasm(lar)ini yuboring. Tugagach /done deb yozing.")

@router.message(AdminProd.photos, F.photo)
async def adm_prod_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"✅ Rasm qo'shildi ({len(photos)}). Yana yuboring yoki /done deb tugating.")

@router.message(AdminProd.photos, Command("done"))
async def adm_prod_done(message: Message, state: FSMContext):
    data = await state.get_data()
    cur = await db.execute(
        "INSERT INTO products(category_id, name, description, price, old_price, quantity, photos, sku) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            data["category_id"], data["name"], data.get("description"),
            data.get("price"), data.get("old_price"), data.get("quantity", 0),
            json.dumps(data.get("photos", [])), "",
        ),
    )
    pid = cur.lastrowid
    await db.execute("UPDATE products SET sku=? WHERE id=?", (f"#GM-{100 + pid}", pid))
    await db.commit()
    await state.clear()
    await message.answer("✅ Mahsulot qo'shildi!", reply_markup=kb_admin())

@router.callback_query(F.data.startswith("adm_prod_edit:"))
async def adm_prod_edit(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    p = await (await db.execute("SELECT * FROM products WHERE id=?", (pid,))).fetchone()
    text = (
        f"<b>{p['name']}</b> ({p['sku']})\n{p['description'] or ''}\n"
        f"Narx: {fmt_price(p['price'])}  Ombor: {p['quantity']}  Top: {'✅' if p['is_top'] else '➖'}"
    )
    await call.message.answer(text, reply_markup=ikb([
        [("💰 Narxini o'zgartirish", f"adm_prod_set:{pid}:price")],
        [("📦 Ombor sonini yangilash", f"adm_prod_set:{pid}:qty")],
        [("📝 Tavsifini o'zgartirish", f"adm_prod_set:{pid}:desc")],
        [("🖼 Rasmlarini almashtirish", f"adm_prod_set:{pid}:photos")],
        [("🔥 Top belgisini almashtirish", f"adm_prod_toptoggle:{pid}")],
        [("🗑 O'chirish", f"adm_prod_del:{pid}")],
    ]))
    await call.answer()

@router.callback_query(F.data.startswith("adm_prod_toptoggle:"))
async def adm_prod_toptoggle(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    p = await (await db.execute("SELECT * FROM products WHERE id=?", (pid,))).fetchone()
    await db.execute("UPDATE products SET is_top=? WHERE id=?", (0 if p["is_top"] else 1, pid))
    await db.commit()
    await call.answer("Yangilandi")

@router.callback_query(F.data.startswith("adm_prod_del:"))
async def adm_prod_del(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    await db.execute("DELETE FROM products WHERE id=?", (pid,))
    await db.commit()
    await call.message.answer("✅ Mahsulot o'chirildi.")
    await call.answer()

@router.callback_query(F.data.startswith("adm_prod_set:"))
async def adm_prod_set(call: CallbackQuery, state: FSMContext):
    _, pid, field = call.data.split(":")
    await state.update_data(product_id=int(pid), field=field, photos=[])
    await state.set_state(AdminProd.edit_value)
    prompts = {
        "price": "Yangi narxni kiriting (raqam):",
        "qty": "Yangi ombor sonini kiriting (raqam):",
        "desc": "Yangi tavsifni yozing:",
        "photos": "Yangi rasm(lar)ni yuboring, tugagach /done deb yozing:",
    }
    await call.message.answer(prompts[field])
    await call.answer()

@router.message(AdminProd.edit_value, F.photo)
async def adm_prod_edit_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("field") != "photos":
        return
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"✅ Qabul qilindi ({len(photos)}). Yana yuboring yoki /done.")

@router.message(AdminProd.edit_value, Command("done"))
async def adm_prod_edit_photos_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("field") != "photos":
        return
    await db.execute(
        "UPDATE products SET photos=? WHERE id=?", (json.dumps(data.get("photos", [])), data["product_id"])
    )
    await db.commit()
    await state.clear()
    await message.answer("✅ Rasmlar yangilandi.", reply_markup=kb_admin())

@router.message(AdminProd.edit_value)
async def adm_prod_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("field")
    pid = data["product_id"]
    if field == "price":
        try:
            price = float(message.text.strip().replace(" ", ""))
        except ValueError:
            await message.answer("Faqat raqam kiriting.")
            return
        await db.execute("UPDATE products SET price=? WHERE id=?", (price, pid))
    elif field == "qty":
        try:
            qty = int(message.text.strip())
        except ValueError:
            await message.answer("Faqat butun son kiriting.")
            return
        old = await (await db.execute("SELECT quantity FROM products WHERE id=?", (pid,))).fetchone()
        await db.execute("UPDATE products SET quantity=? WHERE id=?", (qty, pid))
        if old["quantity"] == 0 and qty > 0:
            await _notify_waiting_customers(pid)
    elif field == "desc":
        await db.execute("UPDATE products SET description=? WHERE id=?", (message.text.strip(), pid))
    await db.commit()
    await state.clear()
    await message.answer("✅ Yangilandi.", reply_markup=kb_admin())

async def _notify_waiting_customers(product_id):
    p = await (await db.execute("SELECT * FROM products WHERE id=?", (product_id,))).fetchone()
    cur = await db.execute("SELECT user_id FROM notify_stock WHERE product_id=?", (product_id,))
    rows = await cur.fetchall()
    for r in rows:
        try:
            await bot.send_message(r["user_id"], f"🔔 Siz kutgan «{p['name']}» mebeli yana omborga keldi!")
        except Exception:
            pass
    await db.execute("DELETE FROM notify_stock WHERE product_id=?", (product_id,))
    await db.commit()

# ---------- Stock Controls ----------

@router.message(F.text == "📦 Ombor nazorati")
async def adm_zero_stock(message: Message):
    if not await is_admin(message.from_user.id):
        return
    cur = await db.execute("SELECT * FROM products WHERE quantity=0")
    rows = await cur.fetchall()
    if not rows:
        await message.answer("Hozircha omborda tugagan mahsulotlar yo'q.")
    else:
        text = "📦 <b>Omborda tugagan mahsulotlar:</b>\n\n" + "\n".join(f"• {p['name']} ({p['sku']})" for p in rows)
        await message.answer(text)

# ---------- Settings ----------

@router.message(F.text == "⚙️ Sozlamalar")
async def adm_settings(message: Message):
    if not await is_admin(message.from_user.id):
        return
    is_open = await get_setting("is_open")
    await message.answer(
        "⚙️ Sozlamalar:",
        reply_markup=ikb([
            [("☎️ Telefonni o'zgartirish", "adm_set_phone")],
            [("📍 Lokatsiyani o'zgartirish", "adm_set_location")],
            [("🆘 Yordam matnini o'zgartirish", "adm_set_help")],
            [(f"🕒 Ish vaqti: {'✅ Ochiq' if is_open == '1' else '🌙 Yopiq'} (bosing)", "adm_set_workhours")],
        ]),
    )

@router.callback_query(F.data == "adm_set_workhours")
async def adm_set_workhours(call: CallbackQuery):
    cur_val = await get_setting("is_open")
    new_val = "0" if cur_val == "1" else "1"
    await set_setting("is_open", new_val)
    await call.answer("Yangilandi: " + ("Ochiq ✅" if new_val == "1" else "Yopiq 🌙"), show_alert=True)

@router.callback_query(F.data == "adm_set_phone")
async def adm_set_phone(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsFSM.phone)
    await call.message.answer("Yangi telefon raqamini yozing:")
    await call.answer()

@router.message(AdminSettingsFSM.phone)
async def adm_set_phone_save(message: Message, state: FSMContext):
    await set_setting("workshop_phone", message.text.strip())
    await state.clear()
    await message.answer("✅ Yangilandi.", reply_markup=kb_admin())

@router.callback_query(F.data == "adm_set_location")
async def adm_set_location(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsFSM.location)
    await call.message.answer("Ustaxona lokatsiyasini yuboring:", reply_markup=kb_location())
    await call.answer()

@router.message(AdminSettingsFSM.location, F.location)
async def adm_set_location_save(message: Message, state: FSMContext):
    await set_setting("workshop_lat", str(message.location.latitude))
    await set_setting("workshop_lon", str(message.location.longitude))
    await state.clear()
    await message.answer("✅ Yangilandi.", reply_markup=kb_admin())

@router.callback_query(F.data == "adm_set_help")
async def adm_set_help(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsFSM.help_text)
    await call.message.answer("Yangi yordam matnini yozing:")
    await call.answer()

@router.message(AdminSettingsFSM.help_text)
async def adm_set_help_save(message: Message, state: FSMContext):
    await set_setting("help_text", message.text.strip())
    await state.clear()
    await message.answer("✅ Yordam matni yangilandi.", reply_markup=kb_admin())

# ---------- Portfolio ----------

@router.message(F.text == "🖼 Portfolio")
async def adm_portfolio(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await message.answer(
        "🖼 Portfolio uchun rasm yuboring, izoh sifatida caption yozing (masalan: 'portfolio: 2024-yil yotoqxona to'plami').",
    )

@router.message(F.photo, F.caption.startswith("portfolio:"))
async def adm_portfolio_add(message: Message):
    if not await is_admin(message.from_user.id):
        return
    caption = message.caption.split("portfolio:", 1)[1].strip()
    await db.execute(
        "INSERT INTO portfolio(photo_file_id, caption) VALUES (?,?)", (message.photo[-1].file_id, caption)
    )
    await db.commit()
    await message.answer("✅ Portfolioga qo'shildi.")

# ---------- Broadcast ----------

@router.message(F.text == "📣 Xabar yuborish")
async def adm_broadcast(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await message.answer(
        "Kimga xabar yuborilsin?",
        reply_markup=ikb([[("👥 Hammaga", "bc:all")], [("🛠 Faqat ustalarga", "bc:workers")]]),
    )

@router.callback_query(F.data.startswith("bc:"))
async def adm_broadcast_target(call: CallbackQuery, state: FSMContext):
    target = call.data.split(":")[1]
    await state.update_data(target=target)
    await state.set_state(AdminBroadcastFSM.message)
    await call.message.answer("Xabar matnini yuboring:")
    await call.answer()

@router.message(AdminBroadcastFSM.message)
async def adm_broadcast_send(message: Message, state: FSMContext):
    data = await state.get_data()
    if data["target"] == "workers":
        cur = await db.execute("SELECT tg_id FROM users WHERE role IN ('worker','admin')")
    else:
        cur = await db.execute("SELECT tg_id FROM users")
    rows = await cur.fetchall()
    ok, fail = 0, 0
    for r in rows:
        try:
            await bot.copy_message(r["tg_id"], message.chat.id, message.message_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await state.clear()
    await message.answer(f"✅ Yuborildi: {ok}, xatolik: {fail}", reply_markup=kb_admin())

# ---------- Statistics ----------

@router.message(F.text == "📊 Statistika")
async def adm_stats(message: Message):
    if not await is_admin(message.from_user.id):
        return
    total_users = (await (await db.execute("SELECT COUNT(*) n FROM users")).fetchone())["n"]
    total_orders = (await (await db.execute("SELECT COUNT(*) n FROM orders")).fetchone())["n"]
    revenue = (await (await db.execute(
        "SELECT COALESCE(SUM(total),0) s FROM orders WHERE status='yopildi'"
    )).fetchone())["s"]
    now = datetime.now()
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()
    d = (await (await db.execute("SELECT COUNT(*) n FROM orders WHERE created_at>=?", (day_ago,))).fetchone())["n"]
    w = (await (await db.execute("SELECT COUNT(*) n FROM orders WHERE created_at>=?", (week_ago,))).fetchone())["n"]
    m = (await (await db.execute("SELECT COUNT(*) n FROM orders WHERE created_at>=?", (month_ago,))).fetchone())["n"]
    top = await (await db.execute(
        "SELECT name, SUM(qty) q FROM order_items GROUP BY product_id ORDER BY q DESC LIMIT 3"
    )).fetchall()
    zero_stock = (await (await db.execute("SELECT COUNT(*) n FROM products WHERE quantity=0")).fetchone())["n"]

    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"Jami foydalanuvchilar: {total_users}\n"
        f"Jami buyurtmalar: {total_orders}\n"
        f"Yopilgan buyurtmalar summasi: {fmt_price(revenue)}\n\n"
        f"So'nggi 24 soat: {d} ta\nSo'nggi 7 kun: {w} ta\nSo'nggi 30 kun: {m} ta\n\n"
        f"Omborda tugagan mahsulotlar: {zero_stock} ta\n\n"
        f"🔥 Eng ko'p sotilgan:\n" + "\n".join(f"• {t['name']} — {t['q']} dona" for t in top)
    )
    await message.answer(text)

# ============================== BACKGROUND: AUTO BACKUP ======================

async def auto_backup_loop():
    while True:
        await asyncio.sleep(7 * 24 * 3600)
        try:
            await db.commit()
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_document(admin_id, FSInputFile(DB_PATH), caption="🗄 Haftalik zaxira nusxa")
                except Exception:
                    pass
        except Exception as e:
            log.exception("Backup xatosi: %s", e)

# ============================== FALLBACK =====================================

@router.message()
async def fallback(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Iltimos, /start bosing.")
        return
    await message.answer("Iltimos, menyudan foydalaning 👇", reply_markup=await kb_main(message.from_user.id))

# ============================== MAIN =========================================

async def main():
    await init_db()
    asyncio.create_task(auto_backup_loop())
    log.info("Bot ishga tushdi.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
