import asyncio
import logging
import time
import sqlite3
import math
import random
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# 0. SOZLAMALAR
# ==========================================
BOT_TOKEN = "8919365987:AAGrk40jcCBExtEj8_vDQhwk6OV8xzwpXYo"
WORKER_GROUP_ID = 8488028783  # Ustalarga xabar boradigan guruh ID sini yozasiz (yoki admin ID)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==========================================
# 1. MA'LUMOTLAR BAZASI (SQLITE3)
# ==========================================
def connect_db():
    return sqlite3.connect('gold_mebel.db')

def create_tables():
    conn = connect_db()
    cursor = conn.cursor()

    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            full_name TEXT,
            phone_number TEXT,
            role TEXT DEFAULT 'user',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT,
            description TEXT,
            price INTEGER,
            quantity INTEGER DEFAULT 0,
            photo_id TEXT,
            is_top BOOLEAN DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            product_id INTEGER,
            quantity INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            telegram_id INTEGER,
            worker_id INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            total_price INTEGER,
            distance_km REAL,
            lat REAL,
            lon REAL
        );
        CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        );
    ''')
    
    default_settings = [
        ('workshop_phone', '+998901234567'),
        ('workshop_lat', '40.2064'), # Bekobod koordinatalari taxminiy
        ('workshop_lon', '69.2682'),
        ('is_open', '1') 
    ]
    cursor.executemany('INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)', default_settings)
    conn.commit()
    conn.close()

# Masofa hisoblash formulasi (Haversine)
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 # Yer radiusi (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

# ==========================================
# 2. MIDDLEWARE (SPAMDAN HIMOYA)
# ==========================================
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit=1.0):
        self.limit = time_limit
        self.users = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        now = time.time()
        if user_id in self.users:
            if (now - self.users[user_id]) < self.limit:
                return 
        self.users[user_id] = now
        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware())
dp.callback_query.middleware(ThrottlingMiddleware())

# ==========================================
# 3. HOLATLAR (STATE)
# ==========================================
class RegisterState(StatesGroup):
    phone = State()

class AdminCategory(StatesGroup):
    name = State()

class AdminProduct(StatesGroup):
    category_id = State()
    name = State()
    desc = State()
    price = State()
    qty = State()
    photo = State()

class CheckoutState(StatesGroup):
    location = State()

class CustomProjectState(StatesGroup):
    photo = State()
    desc = State()

# ==========================================
# 4. TUGMALAR
# ==========================================
def get_main_menu(role="user"):
    kb = [
        [KeyboardButton(text="🪑 Katalog"), KeyboardButton(text="🛒 Savat")],
        [KeyboardButton(text="📦 Buyurtmalarim"), KeyboardButton(text="🖼 O'z loyihamni yuborish")]
    ]
    if role in ["admin", "worker"]:
        kb.append([KeyboardButton(text="⚙️ Admin/Usta Paneli")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def admin_menu():
    kb = [
        [KeyboardButton(text="➕ Bo'lim qo'shish"), KeyboardButton(text="➕ Mebel qo'shish")],
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="👥 Foydalanuvchilar")],
        [KeyboardButton(text="🔙 Bosh menyu")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ==========================================
# 5. ASOSIY BUYRUQLAR (USER QISMI)
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    conn = connect_db()
    user = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    if user:
        await message.answer("Xush kelibsiz!", reply_markup=get_main_menu(user[0]))
    else:
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]], resize_keyboard=True)
        await message.answer("Ro'yxatdan o'tish uchun telefon raqamingizni yuboring:", reply_markup=kb)
        await state.set_state(RegisterState.phone)

@dp.message(RegisterState.phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    conn = connect_db()
    conn.execute("INSERT INTO users (telegram_id, full_name, phone_number) VALUES (?, ?, ?)",
                 (message.from_user.id, message.from_user.full_name, message.contact.phone_number))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("Muvaffaqiyatli ro'yxatdan o'tdingiz!", reply_markup=get_main_menu("user"))

# --- KATALOG VA MAHSULOTLAR ---
@dp.message(F.text == "🪑 Katalog")
async def show_categories(message: Message):
    conn = connect_db()
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()
    
    if not cats:
        return await message.answer("Hozircha bo'limlar yo'q.")
        
    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=cat[1], callback_data=f"cat_{cat[0]}")
    builder.adjust(2)
    await message.answer("Bo'limni tanlang:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("cat_"))
async def show_products(callback: CallbackQuery):
    cat_id = callback.data.split("_")[1]
    conn = connect_db()
    prods = conn.execute("SELECT id, name, price FROM products WHERE category_id = ?", (cat_id,)).fetchall()
    conn.close()
    
    if not prods:
        return await callback.answer("Bu bo'limda mebellar yo'q.", show_alert=True)

    builder = InlineKeyboardBuilder()
    for p in prods:
        builder.button(text=f"{p[1]} - {p[2]:,} so'm", callback_data=f"prod_{p[0]}")
    builder.button(text="🔙 Ortga", callback_data="back_to_cats")
    builder.adjust(1)
    await callback.message.edit_text("Mebelni tanlang:", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "back_to_cats")
async def back_to_cats(callback: CallbackQuery):
    await callback.message.delete()
    await show_categories(callback.message)

@dp.callback_query(F.data.startswith("prod_"))
async def show_product_detail(callback: CallbackQuery):
    prod_id = callback.data.split("_")[1]
    conn = connect_db()
    p = conn.execute("SELECT name, description, price, quantity, photo_id FROM products WHERE id = ?", (prod_id,)).fetchone()
    conn.close()
    
    status = "✅ Omborda bor" if p[3] > 0 else "🛠 Yasab beramiz (Omborda yo'q)"
    text = f"🪑 **{p[0]}**\n\n📝 {p[1]}\n💰 Narxi: {p[2]:,} so'm\n📦 Holati: {status}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Savatga qo'shish", callback_data=f"addcart_{prod_id}")
    
    await callback.message.delete()
    await callback.message.answer_photo(photo=p[4], caption=text, parse_mode="Markdown", reply_markup=builder.as_markup())

# --- SAVAT VA BUYURTMA ---
@dp.callback_query(F.data.startswith("addcart_"))
async def add_to_cart(callback: CallbackQuery):
    prod_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    conn = connect_db()
    # Savatda bormi tekshiramiz
    item = conn.execute("SELECT quantity FROM cart WHERE telegram_id = ? AND product_id = ?", (user_id, prod_id)).fetchone()
    if item:
        conn.execute("UPDATE cart SET quantity = quantity + 1 WHERE telegram_id = ? AND product_id = ?", (user_id, prod_id))
    else:
        conn.execute("INSERT INTO cart (telegram_id, product_id) VALUES (?, ?)", (user_id, prod_id))
    conn.commit()
    conn.close()
    
    await callback.answer("✅ Savatga qo'shildi!", show_alert=True)

@dp.message(F.text == "🛒 Savat")
async def show_cart(message: Message):
    user_id = message.from_user.id
    conn = connect_db()
    items = conn.execute('''
        SELECT c.product_id, p.name, p.price, c.quantity 
        FROM cart c JOIN products p ON c.product_id = p.id 
        WHERE c.telegram_id = ?
    ''', (user_id,)).fetchall()
    conn.close()
    
    if not items:
        return await message.answer("🛒 Savatingiz bo'sh.")

    text = "🛒 **Sizning savatingiz:**\n\n"
    total = 0
    builder = InlineKeyboardBuilder()
    
    for i, item in enumerate(items, 1):
        total += item[2] * item[3]
        text += f"{i}. {item[1]} x {item[3]} dona = {(item[2]*item[3]):,} so'm\n"
        builder.button(text=f"❌ {item[1]} ni olib tashlash", callback_data=f"delcart_{item[0]}")
        
    text += f"\n💰 **Jami: {total:,} so'm**"
    
    builder.button(text="🧹 Savatni tozalash", callback_data="clearcart")
    builder.button(text="✅ Buyurtma berish (Lokatsiya yuborish)", callback_data="checkout")
    builder.adjust(1)
    
    await message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delcart_"))
async def delete_cart_item(callback: CallbackQuery):
    prod_id = callback.data.split("_")[1]
    conn = connect_db()
    conn.execute("DELETE FROM cart WHERE telegram_id = ? AND product_id = ?", (callback.from_user.id, prod_id))
    conn.commit()
    conn.close()
    await callback.message.delete()
    await show_cart(callback.message)

@dp.callback_query(F.data == "clearcart")
async def clear_cart(callback: CallbackQuery):
    conn = connect_db()
    conn.execute("DELETE FROM cart WHERE telegram_id = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    await callback.message.delete()
    await callback.answer("Savat tozalandi", show_alert=True)

# --- CHECKOUT & MASOFA HISBOLASH ---
@dp.callback_query(F.data == "checkout")
async def start_checkout(callback: CallbackQuery, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📍 Lokatsiyamni yuborish", request_location=True)], [KeyboardButton(text="Bekor qilish")]], resize_keyboard=True)
    await callback.message.delete()
    await callback.message.answer("Masofani hisoblash va yetkazib berish uchun lokatsiyangizni yuboring:", reply_markup=kb)
    await state.set_state(CheckoutState.location)

@dp.message(CheckoutState.location, F.location)
async def process_checkout(message: Message, state: FSMContext):
    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    
    conn = connect_db()
    # Ustaxona lokatsiyasi
    ws_lat = float(conn.execute("SELECT setting_value FROM settings WHERE setting_key = 'workshop_lat'").fetchone()[0])
    ws_lon = float(conn.execute("SELECT setting_value FROM settings WHERE setting_key = 'workshop_lon'").fetchone()[0])
    
    # Masofani hisoblash
    distance = calculate_distance(ws_lat, ws_lon, lat, lon)
    
    # Buyurtma yaratish
    order_code = f"#B-{random.randint(1000, 9999)}"
    
    # Savatni o'qish va narxni hisoblash
    items = conn.execute('SELECT p.name, p.price, c.quantity FROM cart c JOIN products p ON c.product_id = p.id WHERE c.telegram_id = ?', (user_id,)).fetchall()
    total_price = sum(i[1] * i[2] for i in items)
    
    conn.execute("INSERT INTO orders (order_code, telegram_id, total_price, distance_km, lat, lon) VALUES (?, ?, ?, ?, ?, ?)",
                 (order_code, user_id, total_price, distance, lat, lon))
                 
    conn.execute("DELETE FROM cart WHERE telegram_id = ?", (user_id,))
    conn.commit()
    
    # Mijoz ma'lumotlari
    user_info = conn.execute("SELECT full_name, phone_number FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    
    await state.clear()
    
    # Mijozga javob
    await message.answer(f"✅ Buyurtmangiz qabul qilindi!\n🔢 Kod: {order_code}\n📏 Ustaxonadan sizgacha masofa: {distance} km\nTez orada aloqaga chiqamiz.", reply_markup=get_main_menu("user"))
    
    # Usta/Adminga xabar
    if WORKER_GROUP_ID:
        items_text = "\n".join([f"- {i[0]} x {i[2]} dona" for i in items])
        admin_text = f"🚨 **YANGI BUYURTMA! {order_code}**\n\n👤 Mijoz: {user_info[0]}\n📞 Tel: {user_info[1]}\n📏 Masofa: {distance} km\n\n📦 Mahsulotlar:\n{items_text}\n💰 Jami: {total_price:,} so'm"
        
        # Lokatsiya linki yaratish
        nav_kb = InlineKeyboardBuilder()
        nav_kb.button(text="🗺 Xaritada ochish (Yo'nalish)", url=f"https://yandex.com/maps/?pt={lon},{lat}&z=18&l=map")
        nav_kb.button(text="✅ Qabul qilish", callback_data=f"accept_{order_code}")
        
        await bot.send_message(chat_id=WORKER_GROUP_ID, text=admin_text, parse_mode="Markdown", reply_markup=nav_kb.as_markup())

# --- O'Z LOYIHASINI YUBORISH ---
@dp.message(F.text == "🖼 O'z loyihamni yuborish")
async def custom_project_start(message: Message, state: FSMContext):
    await message.answer("Sizga qanday mebel kerak? Rasm yoki chizmasini yuboring (Pinterest, Instagram va hokazo):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(CustomProjectState.photo)

@dp.message(CustomProjectState.photo, F.photo)
async def custom_project_photo(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Endi o'lchamlari yoki qo'shimcha talablaringizni yozing:")
    await state.set_state(CustomProjectState.desc)

@dp.message(CustomProjectState.desc, F.text)
async def custom_project_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    
    conn = connect_db()
    conn.execute("INSERT INTO custom_projects (telegram_id, photo_id, description) VALUES (?, ?, ?)", (user_id, data['photo'], message.text))
    user_info = conn.execute("SELECT full_name, phone_number FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer("✅ Loyihangiz ustalarga yuborildi. Narxini hisoblab sizga aloqaga chiqamiz!", reply_markup=get_main_menu("user"))
    
    if WORKER_GROUP_ID:
        await bot.send_photo(chat_id=WORKER_GROUP_ID, photo=data['photo'], 
                             caption=f"🛠 **YANGI MAXSUS LOYIHA**\n\n👤 {user_info[0]} ({user_info[1]})\n📝 Talab: {message.text}", parse_mode="Markdown")


# ==========================================
# 6. ADMIN PANEL (Kategoriya va Mebel qo'shish)
# ==========================================
@dp.message(F.text == "⚙️ Admin/Usta Paneli")
async def admin_panel_open(message: Message):
    user_id = message.from_user.id
    conn = connect_db()
    role = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (user_id,)).fetchone()[0]
    conn.close()
    
    if role in ["admin", "worker"]:
        await message.answer("Admin panelga xush kelibsiz:", reply_markup=admin_menu())

@dp.message(F.text == "🔙 Bosh menyu")
async def back_to_main(message: Message):
    user_id = message.from_user.id
    conn = connect_db()
    role = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (user_id,)).fetchone()[0]
    conn.close()
    await message.answer("Asosiy menyu:", reply_markup=get_main_menu(role))

# --- KATEGORIYA QO'SHISH ---
@dp.message(F.text == "➕ Bo'lim qo'shish")
async def add_cat_start(message: Message, state: FSMContext):
    await message.answer("Yangi bo'lim nomini yozing (Masalan: Oshxona mebellari):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AdminCategory.name)

@dp.message(AdminCategory.name)
async def add_cat_save(message: Message, state: FSMContext):
    conn = connect_db()
    try:
        conn.execute("INSERT INTO categories (name) VALUES (?)", (message.text,))
        conn.commit()
        await message.answer(f"✅ '{message.text}' bo'limi yaratildi!", reply_markup=admin_menu())
    except sqlite3.IntegrityError:
        await message.answer("❌ Bunday bo'lim allaqachon mavjud.", reply_markup=admin_menu())
    conn.close()
    await state.clear()

# --- MEBEL QO'SHISH ---
@dp.message(F.text == "➕ Mebel qo'shish")
async def add_prod_cat(message: Message, state: FSMContext):
    conn = connect_db()
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()
    
    if not cats:
        return await message.answer("Avval bo'lim yarating!")
        
    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=cat[1], callback_data=f"setcat_{cat[0]}")
    builder.adjust(2)
    
    await message.answer("Qaysi bo'limga mebel qo'shamiz?", reply_markup=builder.as_markup())
    await state.set_state(AdminProduct.category_id)

@dp.callback_query(AdminProduct.category_id, F.data.startswith("setcat_"))
async def add_prod_name(callback: CallbackQuery, state: FSMContext):
    cat_id = callback.data.split("_")[1]
    await state.update_data(category_id=cat_id)
    await callback.message.delete()
    await callback.message.answer("Mebel nomini kiriting:")
    await state.set_state(AdminProduct.name)

@dp.message(AdminProduct.name)
async def add_prod_desc(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Mebel haqida ma'lumot (tavsif) yozing:")
    await state.set_state(AdminProduct.desc)

@dp.message(AdminProduct.desc)
async def add_prod_price(message: Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("Narxini raqamda yozing (Masalan: 1500000):")
    await state.set_state(AdminProduct.price)

@dp.message(AdminProduct.price)
async def add_prod_qty(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Faqat raqam kiriting!")
    await state.update_data(price=int(message.text))
    await message.answer("Omborda nechta bor? (Agar yasab beriladigan bo'lsa 0 yozing):")
    await state.set_state(AdminProduct.qty)

@dp.message(AdminProduct.qty)
async def add_prod_photo(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Faqat raqam kiriting!")
    await state.update_data(qty=int(message.text))
    await message.answer("Mebelning chiroyli rasmini yuboring:")
    await state.set_state(AdminProduct.photo)

@dp.message(AdminProduct.photo, F.photo)
async def add_prod_save(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = connect_db()
    conn.execute('''
        INSERT INTO products (category_id, name, description, price, quantity, photo_id) 
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (data['category_id'], data['name'], data['desc'], data['price'], data['qty'], message.photo[-1].file_id))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer(f"✅ {data['name']} bazaga muvaffaqiyatli qo'shildi!", reply_markup=admin_menu())


# ==========================================
# 7. ISHGA TUSHIRISH
# ==========================================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("Gold Mebel tizimi ishga tushmoqda...")
    create_tables()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot o'chirildi.")
