import asyncio
import sqlite3
import math
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# 1. ASOSIY SOZLAMALAR VA XAVFSIZLIK
# ==========================================
BOT_TOKEN = "8919365987:AAGrk40jcCBExtEj8_vDQhwk6OV8xzwpXYo"
OWNER_ID = 8488028783  

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

WORKSHOP_LAT = 41.2995
WORKSHOP_LON = 69.2401

def connect_db():
    return sqlite3.connect('gold_mebel.db')

def create_tables():
    conn = connect_db()
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, full_name TEXT, role TEXT DEFAULT 'user');
        CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER, name TEXT, price INTEGER);
    ''')
    conn.commit()
    conn.close()

# Foydalanuvchi rolini aniqlash (Server o'chsa ham OWNER doim admin bo'lib qoladi)
def get_user_role(user_id):
    if user_id == OWNER_ID:
        return 'admin'
    conn = connect_db()
    user = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    return user[0] if user else 'user'

async def send_to_all_admins(text, reply_markup=None):
    conn = connect_db()
    staff = conn.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'worker')").fetchall()
    conn.close()
    
    # Har doim OWNER_ID ga ham borishini ta'minlaymiz
    ids = list(set([s[0] for s in staff] + [OWNER_ID]))
    for chat_id in ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        except:
            continue

# ==========================================
# 2. MIDDLEWARE (SPAMDAN HIMOYA)
# ==========================================
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self):
        self.users = {}
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        if user_id in self.users:
            return
        self.users[user_id] = True
        await asyncio.sleep(0.5)
        del self.users[user_id]
        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware())

# ==========================================
# 3. FSM HOLATLAR (STATES)
# ==========================================
class CRMState(StatesGroup):
    waiting_reply = State()

class AdminState(StatesGroup):
    waiting_category_name = State()
    waiting_broadcast_text = State()
    waiting_worker_id = State()

# ==========================================
# 4. START VA MENYULAR
# ==========================================
def get_main_menu(user_id):
    role = get_user_role(user_id)
    
    keyboard = [
        [KeyboardButton(text="🪑 Katalog"), KeyboardButton(text="📍 Lokatsiya yuborish", request_location=True)]
    ]
    
    # Rolga qarab shaxsiy tugma chiqadi
    if role == 'admin':
        keyboard.append([KeyboardButton(text="⚙️ Admin Paneli")])
    elif role == 'worker':
        keyboard.append([KeyboardButton(text="🛠️ Usta Paneli")])
        
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@dp.message(CommandStart())
async def cmd_start(message: Message):
    conn = connect_db()
    user = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (message.from_user.id,)).fetchone()
    if not user:
        role = 'admin' if message.from_user.id == OWNER_ID else 'user'
        conn.execute("INSERT INTO users (telegram_id, full_name, role) VALUES (?, ?, ?)", 
                     (message.from_user.id, message.from_user.full_name, role))
        conn.commit()
    conn.close()
    
    await message.answer("✨ Gold Mebel botiga xush kelibsiz! Bizga xabar yozishingiz mumkin.", 
                         reply_markup=get_main_menu(message.from_user.id))

# ==========================================
# 5. ADMIN VA USTA PANELLARI
# ==========================================
@dp.message(F.text == "⚙️ Admin Paneli")
async def admin_panel(message: Message):
    if get_user_role(message.from_user.id) == 'admin':
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 Xabar yuborish (Sms tarqatish)", callback_data="admin_broadcast")
        kb.button(text="➕ Kategoriya qo'shish", callback_data="add_category")
        kb.button(text="➕ Usta tayinlash (ID orqali)", callback_data="add_worker")
        kb.adjust(1)
        await message.answer("⚙️ **Bosh Admin Paneli**\nKerakli bo'limni tanlang:", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.message(F.text == "🛠️ Usta Paneli")
async def worker_panel(message: Message):
    if get_user_role(message.from_user.id) == 'worker':
        await message.answer("🛠️ **Usta ishchi paneli**\nSizga mijozlardan kelgan xabarlar va buyurtmalar avtomatik kelib tushadi.")

# ==========================================
# 6. XABAR YUBORISH (BROADCAST) TIZIMI
# ==========================================
@dp.callback_query(F.data == "admin_broadcast")
async def choose_broadcast_target(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Faqat Adminlarga", callback_data="target_admin")
    kb.button(text="🛠️ Faqat Ustalarga", callback_data="target_worker")
    kb.button(text="👥 Admin + Ustalarga", callback_data="target_staff")
    kb.button(text="🌍 Hamma foydalanuvchilarga", callback_data="target_all")
    kb.adjust(1)
    await callback.message.answer("Xabar kimlarga yuborilsin? Tanlang:", reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("target_"))
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    target = callback.data.split("_")[1]
    await state.update_data(broadcast_target=target)
    await callback.message.answer("✍️ Yubormoqchi bo'lgan xabaringiz matnini kiriting:")
    await state.set_state(AdminState.waiting_broadcast_text)
    await callback.answer()

@dp.message(AdminState.waiting_broadcast_text)
async def do_broadcast(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("broadcast_target")
    text = f"📢 **Tizimdan e'lon:**\n\n{message.text}"
    
    conn = connect_db()
    if target == "admin":
        users = conn.execute("SELECT telegram_id FROM users WHERE role = 'admin'").fetchall()
    elif target == "worker":
        users = conn.execute("SELECT telegram_id FROM users WHERE role = 'worker'").fetchall()
    elif target == "staff":
        users = conn.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'worker')").fetchall()
    else: # all
        users = conn.execute("SELECT telegram_id FROM users").fetchall()
    conn.close()
    
    # Ro'yxatni tozalab OWNER_ID ni qo'shamiz agar target admin/staff/all bo'lsa
    user_ids = list(set([u[0] for u in users]))
    if target in ["admin", "staff", "all"] and OWNER_ID not in user_ids:
        user_ids.append(OWNER_ID)
        
    await message.answer("⏳ Xabar tarqatilmoqda, kuting...")
    
    success_count = 0
    for u_id in user_ids:
        try:
            await bot.send_message(chat_id=u_id, text=text, parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.05) # Telegram limitlaridan oshib ketmaslik uchun
        except:
            continue
            
    await message.answer(f"✅ Xabar muvaffaqiyatli tarqatildi!\nJami yetkazildi: {success_count} ta foydalanuvchiga.")
    await state.clear()

# ==========================================
# 7. USTA QO'SHISH MANTIQI
# ==========================================
@dp.callback_query(F.data == "add_worker")
async def start_add_worker(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Usta qilmoqchi bo'lgan shaxsning Telegram ID raqamini yozing:")
    await state.set_state(AdminState.waiting_worker_id)
    await callback.answer()

@dp.message(AdminState.waiting_worker_id)
async def save_worker(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak. Qayta urinib ko'ring:")
        return
    
    worker_id = int(message.text)
    conn = connect_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (worker_id,)).fetchone()
    
    if not user:
        conn.execute("INSERT INTO users (telegram_id, full_name, role) VALUES (?, ?, 'worker')", (worker_id, "Noma'lum Usta"))
    else:
        conn.execute("UPDATE users SET role = 'worker' WHERE telegram_id = ?", (worker_id,))
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(chat_id=worker_id, text="🎉 Siz ushbu botda **Usta (Ishchi)** qilib tayinlandingiz! Menyuni yangilash uchun /start bosing.", parse_mode="Markdown")
    except:
        pass
        
    await message.answer(f"✅ ID: {worker_id} foydalanuvchisi muvaffaqiyatli USTA roliga o'tkazildi.")
    await state.clear()

# ==========================================
# 8. KATEGORIYA QO'SHISH MANTIQI
# ==========================================
@dp.callback_query(F.data == "add_category")
async def add_category_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Yangi kategoriya nomini yozing:")
    await state.set_state(AdminState.waiting_category_name)
    await callback.answer()

@dp.message(AdminState.waiting_category_name)
async def add_category_save(message: Message, state: FSMContext):
    conn = connect_db()
    conn.execute("INSERT INTO categories (name) VALUES (?)", (message.text,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ '{message.text}' kategoriyasi muvaffaqiyatli qo'shildi!")
    await state.clear()

# ==========================================
# 9. LOKATSIYA (HAVERSINE MASOFA)
# ==========================================
@dp.message(F.location)
async def handle_location(message: Message):
    lat = message.location.latitude
    lon = message.location.longitude
    
    # Masofani hisoblash (Haversine)
    R = 6371
    dlat = math.radians(lat - WORKSHOP_LAT)
    dlon = math.radians(lon - WORKSHOP_LON)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(WORKSHOP_LAT)) * math.cos(math.radians(lat)) * math.sin(dlon/2)**2
    distance = round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)
    
    text_to_admin = (f"🚨 **YANGI BUYURTMA LOKATSIYASI**\n\n"
                     f"👤 Mijoz: {message.from_user.full_name}\n"
                     f"📏 Masofa: {distance} km\n\n"
                     f"📍 [Xaritada ko'rish](https://yandex.com/maps/?pt={lon},{lat}&z=16&l=map)")
    
    await send_to_all_admins(text_to_admin)
    await message.answer(f"✅ Lokatsiyangiz qabul qilindi. Masofa: {distance} km. Yaqin orada usta siz bilan bog'lanadi!")

# ==========================================
# 10. CRM CHAT MANTIQI (GURUHSIZ)
# ==========================================
@dp.message(F.chat.type == "private")
async def handle_all_messages(message: Message):
    if message.text in ["🪑 Katalog", "⚙️ Admin Paneli", "🛠️ Usta Paneli"]:
        return
        
    role = get_user_role(message.from_user.id)
    
    if role == 'user':
        kb = InlineKeyboardBuilder()
        kb.button(text="Javob berish ↩️", callback_data=f"reply_{message.from_user.id}")
        
        text_to_admins = f"👤 Mijoz: {message.from_user.full_name}\n📝 Xabar: {message.text}"
        await send_to_all_admins(text=text_to_admins, reply_markup=kb.as_markup())
        await message.answer("✅ Xabaringiz ustalarga yuborildi. Tez orada javob qaytaramiz.")

@dp.callback_query(F.data.startswith("reply_"))
async def crm_start_reply(callback: CallbackQuery, state: FSMContext):
    target_id = callback.data.split("_")[1]
    await state.update_data(target_id=target_id)
    await callback.message.answer("✍️ Mijozga yuboriladigan javob matnini kiriting:")
    await state.set_state(CRMState.waiting_reply)
    await callback.answer()

@dp.message(CRMState.waiting_reply)
async def crm_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get('target_id')
    
    try:
        await bot.send_message(chat_id=target_id, text=f"📩 **Gold Mebel ustaxonasidan javob:**\n\n{message.text}")
        await message.answer("✅ Javobingiz mijozga muvaffaqiyatli yetkazildi!")
    except:
        await message.answer("❌ Xabarni yuborib bo'lmadi. Mijoz botni bloklagan bo'lishi mumkin.")
        
    await state.clear()

# ==========================================
# 11. TIZIMNI ISHGA TUSHIRISH
# ==========================================
async def main():
    create_tables()
    print("Gold Mebel tizimi muvaffaqiyatli ishlamoqda...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
