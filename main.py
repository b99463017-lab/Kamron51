import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# ================= 1. SOZLAMALAR =================
BOT_TOKEN = "8919365987:AAGrk40jcCBExtEj8_vDQhwk6OV8xzwpXYo"
MAIN_ADMIN_ID = 8488028783  # O'zingizning Telegram ID raqamingizni yozing

# Railway'dagi xatoni oldini olish uchun DefaultBotProperties ishlatildi
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= 2. BAZA (SQLite) =================
def init_db():
    conn = sqlite3.connect("mebel.db")
    cur = conn.cursor()
    # Foydalanuvchilar (Mijozlar - xabar jo'natish uchun)
    cur.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT)""")
    # Xodimlar (Admin yoki Usta)
    cur.execute("""CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, role TEXT)""")
    # Bo'limlar va Mahsulotlar
    cur.execute("""CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cat_id INTEGER, name TEXT, desc TEXT, price TEXT, photo_id TEXT
    )""")
    conn.commit()
    conn.close()

def is_admin(user_id):
    if user_id == MAIN_ADMIN_ID: return True
    conn = sqlite3.connect("mebel.db")
    res = conn.execute("SELECT role FROM staff WHERE user_id=? AND role='admin'", (user_id,)).fetchone()
    conn.close()
    return bool(res)

def is_usta(user_id):
    conn = sqlite3.connect("mebel.db")
    res = conn.execute("SELECT role FROM staff WHERE user_id=? AND role='usta'", (user_id,)).fetchone()
    conn.close()
    return bool(res)

# ================= 3. HOLATLAR (FSM) =================
class AddCategory(StatesGroup): name = State()
class AddProduct(StatesGroup): cat_id, photo, name, desc, price = State(), State(), State(), State(), State()
class AddStaff(StatesGroup): user_id, role = State(), State()
class BroadcastMsg(StatesGroup): message = State()
class OrderState(StatesGroup): waiting_for_phone, waiting_for_extra_phone, waiting_for_location = State(), State(), State()

# ================= 4. TUGMALAR =================
def user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛋 Katalog")],
            [KeyboardButton(text="📍 Manzil"), KeyboardButton(text="📞 Aloqa")]
        ], resize_keyboard=True
    )

def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Mahsulot qo'shish"), KeyboardButton(text="📂 Bo'lim qo'shish")],
            [KeyboardButton(text="👥 Xodim qo'shish"), KeyboardButton(text="✉️ Xabar jo'natish")],
            [KeyboardButton(text="🛋 Mijoz menyusi")]
        ], resize_keyboard=True
    )

def usta_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛠 Mening buyurtmalarim")]], resize_keyboard=True
    )

def cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)

def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Asosiy raqamni yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ], resize_keyboard=True
    )

def skip_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ O'tkazib yuborish (yo'q)")], [KeyboardButton(text="❌ Bekor qilish")]], 
        resize_keyboard=True
    )

def location_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ], resize_keyboard=True
    )

# ================= 5. START VA BEKOR QILISH =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    fname = message.from_user.first_name

    # Mijozni bazaga saqlash (Rassilka uchun)
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)", (user_id, fname))
    conn.commit()
    conn.close()

    if is_admin(user_id):
        await message.answer(f"Assalomu alaykum, Admin <b>{fname}</b>!", reply_markup=admin_keyboard())
    elif is_usta(user_id):
        await message.answer(f"Hormang, Usta <b>{fname}</b>!", reply_markup=usta_keyboard())
    else:
        await message.answer(f"Xush kelibsiz, <b>{fname}</b>!\nMebel ustaxonamiz botiga marhamat.", reply_markup=user_keyboard())

@router.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer("Jarayon bekor qilindi.", reply_markup=admin_keyboard())
    elif is_usta(user_id):
        await message.answer("Jarayon bekor qilindi.", reply_markup=usta_keyboard())
    else:
        await message.answer("Jarayon bekor qilindi.", reply_markup=user_keyboard())

# ================= 6. ADMIN: BO'LIM VA MAHSULOT =================
@router.message(F.text == "📂 Bo'lim qo'shish")
async def add_cat(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Yangi bo'lim nomini yozing:", reply_markup=cancel_keyboard())
    await state.set_state(AddCategory.name)

@router.message(AddCategory.name)
async def save_cat(message: Message, state: FSMContext):
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT INTO categories (name) VALUES (?)", (message.text,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Bo'lim qo'shildi!", reply_markup=admin_keyboard())
    await state.clear()

@router.message(F.text == "➕ Mahsulot qo'shish")
async def add_prod_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    conn = sqlite3.connect("mebel.db")
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()
    if not cats:
        return await message.answer("Avval bo'lim yarating!")

    builder = InlineKeyboardBuilder()
    for cat in cats: builder.button(text=cat[1], callback_data=f"addprod_{cat[0]}")
    builder.adjust(2)
    await message.answer("Qaysi bo'limga mahsulot qo'shamiz?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("addprod_"))
async def add_prod_photo(call: CallbackQuery, state: FSMContext):
    await state.update_data(cat_id=call.data.split("_")[1])
    await call.message.delete()
    await call.message.answer("Mahsulotning tiniq rasmini yuboring:", reply_markup=cancel_keyboard())
    await state.set_state(AddProduct.photo)

@router.message(AddProduct.photo, F.photo)
async def add_prod_name(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Mahsulot nomini yozing:")
    await state.set_state(AddProduct.name)

@router.message(AddProduct.name)
async def add_prod_desc(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Ma'lumot (o'lchami, materiali):")
    await state.set_state(AddProduct.desc)

@router.message(AddProduct.desc)
async def add_prod_price(message: Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("Narxini kiriting:")
    await state.set_state(AddProduct.price)

@router.message(AddProduct.price)
async def add_prod_save(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT INTO products (cat_id, name, desc, price, photo_id) VALUES (?, ?, ?, ?, ?)",
                 (data['cat_id'], data['name'], data['desc'], message.text, data['photo']))
    conn.commit()
    conn.close()
    await message.answer_photo(data['photo'], caption=f"✅ Saqlandi!\n🛋 {data['name']}", reply_markup=admin_keyboard())
    await state.clear()

# ================= 7. ADMIN: XODIM VA RASSILKA =================
@router.message(F.text == "👥 Xodim qo'shish")
async def add_staff(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Xodimning Telegram ID sini yozing:", reply_markup=cancel_keyboard())
    await state.set_state(AddStaff.user_id)

@router.message(AddStaff.user_id)
async def add_staff_role(message: Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Faqat raqam kiriting!")
    await state.update_data(user_id=int(message.text))
    builder = ReplyKeyboardBuilder()
    builder.button(text="usta").button(text="admin").button(text="❌ Bekor qilish").adjust(2)
    await message.answer("Rolni tanlang:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(AddStaff.role)

@router.message(AddStaff.role)
async def add_staff_save(message: Message, state: FSMContext):
    role = message.text.lower()
    data = await state.get_data()
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT OR REPLACE INTO staff (user_id, role) VALUES (?, ?)", (data['user_id'], role))
    conn.commit()
    conn.close()
    await message.answer("✅ Xodim saqlandi!", reply_markup=admin_keyboard())
    await state.clear()

@router.message(F.text == "✉️ Xabar jo'natish")
async def broadcast_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Barcha mijozlarga yuboriladigan xabarni kiriting (Rasm ham qo'shishingiz mumkin):", reply_markup=cancel_keyboard())
    await state.set_state(BroadcastMsg.message)

@router.message(BroadcastMsg.message)
async def broadcast_send(message: Message, state: FSMContext):
    conn = sqlite3.connect("mebel.db")
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    
    count = 0
    for user in users:
        try:
            await message.send_copy(chat_id=user[0])
            count += 1
            await asyncio.sleep(0.05) # Telegram limitiga tushmaslik uchun
        except: pass
        
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga muvaffaqiyatli yetkazildi!", reply_markup=admin_keyboard())
    await state.clear()

# ================= 8. MIJOZ: KATALOG =================
@router.message(F.text == "🛋 Mijoz menyusi")
@router.message(F.text == "🛋 Katalog")
async def show_catalog(message: Message):
    conn = sqlite3.connect("mebel.db")
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()
    if not cats: return await message.answer("Katalog bo'sh.")

    builder = InlineKeyboardBuilder()
    for cat in cats: builder.button(text=cat[1], callback_data=f"showcat_{cat[0]}")
    builder.adjust(2)
    await message.answer("Bo'limni tanlang:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("showcat_"))
async def show_products(call: CallbackQuery):
    cat_id = call.data.split("_")[1]
    conn = sqlite3.connect("mebel.db")
    prods = conn.execute("SELECT id, name, desc, price, photo_id FROM products WHERE cat_id=?", (cat_id,)).fetchall()
    conn.close()

    if not prods: return await call.answer("Mahsulot yo'q", show_alert=True)
    await call.message.delete()
    for p in prods:
        caption = f"🛋 <b>{p[1]}</b>\n\n📝 {p[2]}\n\n💰 <b>Narxi:</b> {p[3]}"
        builder = InlineKeyboardBuilder()
        builder.button(text="🛒 Buyurtma berish", callback_data=f"buy_{p[0]}")
        await call.message.answer_photo(photo=p[4], caption=caption, reply_markup=builder.as_markup())

# ================= 9. MIJOZ: BUYURTMA BERISH JARAYONI =================
@router.callback_query(F.data.startswith("buy_"))
async def order_start(call: CallbackQuery, state: FSMContext):
    prod_id = call.data.split("_")[1]
    await state.update_data(prod_id=prod_id)
    await call.message.answer("Buyurtma uchun asosiy telefon raqamingizni yuboring:", reply_markup=phone_keyboard())
    await state.set_state(OrderState.waiting_for_phone)
    await call.answer()

@router.message(OrderState.waiting_for_phone, F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else message.text
    await state.update_data(phone=phone)
    await message.answer("Qo'shimcha raqam kiriting yoli o'tkazib yuboring:", reply_markup=skip_keyboard())
    await state.set_state(OrderState.waiting_for_extra_phone)

@router.message(OrderState.waiting_for_extra_phone)
async def process_extra_phone(message: Message, state: FSMContext):
    extra = message.text if message.text != "⏭ O'tkazib yuborish (yo'q)" else "Kiritilmadi"
    await state.update_data(extra_phone=extra)
    await message.answer("Manzilni xaritadan belgilab yuboring (Lokatsiya):", reply_markup=location_keyboard())
    await state.set_state(OrderState.waiting_for_location)

@router.message(OrderState.waiting_for_location, F.location)
async def process_location(message: Message, state: FSMContext):
    lat, lon = message.location.latitude, message.location.longitude
    data = await state.get_data()
    
    conn = sqlite3.connect("mebel.db")
    prod = conn.execute("SELECT name FROM products WHERE id=?", (data['prod_id'],)).fetchone()
    ustalar = conn.execute("SELECT user_id FROM staff WHERE role='usta'").fetchall()
    conn.close()

    # Mijozga javob
    await message.answer("✅ Buyurtmangiz qabul qilindi! Ustalar tez orada aloqaga chiqishadi.", reply_markup=user_keyboard())

    # Ustalarga jo'natish
    order_text = (
        f"🚨 <b>YANGI BUYURTMA!</b>\n\n"
        f"🛋 <b>Mebel:</b> {prod[0] if prod else 'Noma`lum'}\n"
        f"👤 <b>Mijoz:</b> {message.from_user.full_name}\n"
        f"📱 <b>Raqam:</b> {data['phone']}\n"
        f"📞 <b>Qo'shimcha:</b> {data['extra_phone']}\n\n"
        f"📍 <a href='https://maps.google.com/?q={lat},{lon}'>Google Maps orqali ko'rish</a>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Qabul qildim", callback_data="accept_order")

    for usta in ustalar:
        try:
            await message.bot.send_message(chat_id=usta[0], text=order_text, reply_markup=builder.as_markup())
            await message.bot.send_location(chat_id=usta[0], latitude=lat, longitude=lon)
        except: pass

    await state.clear()

@router.callback_query(F.data == "accept_order")
async def usta_accept(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply(f"✅ Bu buyurtmani Usta {call.from_user.first_name} qabul qildi!")
    await call.answer()

# ================= 10. ISHGA TUSHIRISH =================
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
