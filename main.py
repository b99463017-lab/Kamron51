import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# ================= 1. SOZLAMALAR VA BAZA =================
TOKEN = "8919365987:AAGrk40jcCBExtEj8_vDQhwk6OV8xzwpXYo"
MAIN_ADMIN_ID = 8488028783  # O'zingizning Telegram ID raqamingiz

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()
router = Router()

def init_db():
    with sqlite3.connect("mebel.db") as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, role TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, cat_id INTEGER, name TEXT, desc TEXT, price TEXT, photo_id TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('status', 'open')")
        cur.execute("INSERT OR IGNORE INTO staff (user_id, role) VALUES (?, 'admin')", (MAIN_ADMIN_ID,))
        conn.commit()

def is_admin(user_id):
    with sqlite3.connect("mebel.db") as conn:
        res = conn.execute("SELECT role FROM staff WHERE user_id = ? AND role = 'admin'", (user_id,)).fetchone()
    return bool(res)

# ================= 2. FSM HOLATLAR (States) =================
class AdminState(StatesGroup):
    add_cat = State()
    add_prod_cat = State()
    add_prod_name = State()
    add_prod_photo = State()
    add_prod_desc = State()
    add_prod_price = State()
    add_staff_id = State()

# ================= 3. KLAVIATURALAR =================
def admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📦 Mahsulotlar (Qo'shish/Boshqarish)")
    builder.button(text="📂 Bo'limlar boshqaruvi")
    builder.button(text="👥 Xodimlar (Usta/Admin)")
    builder.button(text="⚙️ Ustaxona sozlamalari")
    builder.button(text="✉️ Xabar jo'natish")
    builder.button(text="🛋 Mijoz menyusiga o'tish")
    builder.adjust(1, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)

# ================= 4. ASOSIY HANDLERLAR =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)", (message.from_user.id, message.from_user.full_name))
        conn.commit()
    
    if is_admin(message.from_user.id):
        await message.answer(f"Xush kelibsiz, Admin {message.from_user.first_name}!\nBoshqaruv panelidasiz:", reply_markup=admin_keyboard())
    else:
        await message.answer("Assalomu alaykum! Mebel buyurtma berish botiga xush kelibsiz. Katalogni ko'rish uchun menyudan foydalaning.")

@router.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Amal bekor qilindi.", reply_markup=admin_keyboard())

# ================= 5. BO'LIMLAR BOSHQARUVI =================
@router.message(F.text == "📂 Bo'limlar boshqaruvi")
async def manage_categories(message: Message):
    if not is_admin(message.from_user.id): return
    with sqlite3.connect("mebel.db") as conn:
        cats = conn.execute("SELECT id, name FROM categories").fetchall()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Yangi bo'lim qo'shish", callback_data="add_category")
    for cat in cats:
        builder.button(text=f"📁 {cat[1]}", callback_data="ignore")
        builder.button(text="🗑 O'chirish", callback_data=f"delcat_{cat[0]}")
    builder.adjust(1, 2)
    await message.answer("📂 Bo'limlarni boshqarish:", reply_markup=builder.as_markup())

@router.callback_query(F.data == "add_category")
async def add_cat_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Yangi bo'lim nomini yozing:", reply_markup=cancel_keyboard())
    await state.set_state(AdminState.add_cat)
    await call.answer()

@router.message(AdminState.add_cat)
async def add_cat_save(message: Message, state: FSMContext):
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("INSERT INTO categories (name) VALUES (?)", (message.text,))
        conn.commit()
    await message.answer(f"✅ Bo'lim qo'shildi: {message.text}", reply_markup=admin_keyboard())
    await state.clear()

@router.callback_query(F.data.startswith("delcat_"))
async def delete_category(call: CallbackQuery):
    cat_id = call.data.split("_")[1]
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        conn.execute("DELETE FROM products WHERE cat_id=?", (cat_id,)) # Mahsulotlari ham o'chadi
        conn.commit()
    await call.message.edit_text("✅ Bo'lim va uning ichidagi mahsulotlar o'chirildi!")
    await call.answer()

# ================= 6. MAHSULOTLAR BOSHQARUVI VA NARX =================
@router.message(F.text == "📦 Mahsulotlar (Qo'shish/Boshqarish)")
async def manage_products(message: Message):
    if not is_admin(message.from_user.id): return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Yangi mahsulot qo'shish", callback_data="add_prod_start")
    builder.button(text="📋 Mahsulotlarni o'chirish", callback_data="list_prods_del")
    builder.adjust(1)
    await message.answer("📦 Mahsulotlar paneli:", reply_markup=builder.as_markup())

@router.callback_query(F.data == "add_prod_start")
async def add_prod_start(call: CallbackQuery, state: FSMContext):
    with sqlite3.connect("mebel.db") as conn:
        cats = conn.execute("SELECT id, name FROM categories").fetchall()
    if not cats:
        return await call.message.answer("Avval bo'lim yarating!", reply_markup=admin_keyboard())
    
    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=cat[1], callback_data=f"selcat_{cat[0]}")
    builder.adjust(2)
    
    await call.message.answer("Mahsulot qaysi bo'limga qo'shiladi?", reply_markup=builder.as_markup())
    await call.answer()

@router.callback_query(F.data.startswith("selcat_"))
async def add_prod_name(call: CallbackQuery, state: FSMContext):
    await state.update_data(cat_id=call.data.split("_")[1])
    await call.message.answer("Mahsulot nomini yozing:", reply_markup=cancel_keyboard())
    await state.set_state(AdminState.add_prod_name)
    await call.answer()

@router.message(AdminState.add_prod_name)
async def add_prod_photo(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Mahsulot rasmini yuboring:")
    await state.set_state(AdminState.add_prod_photo)

@router.message(AdminState.add_prod_photo, F.photo)
async def add_prod_desc(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("Mahsulot haqida ma'lumot yozing (Material, o'lcham):")
    await state.set_state(AdminState.add_prod_desc)

@router.message(AdminState.add_prod_desc)
async def add_prod_price(message: Message, state: FSMContext):
    await state.update_data(desc=message.text)
    
    # MOSLASHUVCHAN NARX (Siz so'ragan xususiyat)
    builder = ReplyKeyboardBuilder()
    builder.button(text="🤝 Kelishiladi")
    builder.button(text="❌ Bekor qilish")
    builder.adjust(1)
    
    await message.answer(
        "Narxni qanday ko'rsatamiz? Ixtiyoriy matn yozing (masalan: 1 kv.m 2mln) yoki pastdagi tugmani bosing:",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(AdminState.add_prod_price)

@router.message(AdminState.add_prod_price)
async def add_prod_save(message: Message, state: FSMContext):
    data = await state.get_data()
    price = message.text
    
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("INSERT INTO products (cat_id, name, desc, price, photo_id) VALUES (?, ?, ?, ?, ?)",
                     (data['cat_id'], data['name'], data['desc'], price, data['photo_id']))
        conn.commit()
    
    cap = f"✅ <b>Saqlandi!</b>\n\n🛋 {data['name']}\n📝 {data['desc']}\n💰 Narxi: {price}"
    await message.answer_photo(data['photo_id'], caption=cap, reply_markup=admin_keyboard())
    await state.clear()

@router.callback_query(F.data == "list_prods_del")
async def list_prods_del(call: CallbackQuery):
    with sqlite3.connect("mebel.db") as conn:
        prods = conn.execute("SELECT id, name FROM products").fetchall()
    
    if not prods: return await call.message.edit_text("Mahsulotlar yo'q.")
    
    builder = InlineKeyboardBuilder()
    for p in prods:
        builder.button(text=p[1], callback_data="ignore")
        builder.button(text="🗑", callback_data=f"delprod_{p[0]}")
    builder.adjust(1, 1)
    await call.message.edit_text("O'chirish uchun mahsulotni tanlang:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("delprod_"))
async def del_product(call: CallbackQuery):
    prod_id = call.data.split("_")[1]
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("DELETE FROM products WHERE id=?", (prod_id,))
        conn.commit()
    await call.message.edit_text("✅ Mahsulot o'chirildi!")
    await call.answer()

# ================= 7. USTAXONA SOZLAMALARI =================
@router.message(F.text == "⚙️ Ustaxona sozlamalari")
async def workshop_settings(message: Message):
    if not is_admin(message.from_user.id): return
    with sqlite3.connect("mebel.db") as conn:
        status = conn.execute("SELECT value FROM settings WHERE key='status'").fetchone()[0]
    
    builder = InlineKeyboardBuilder()
    if status == "open":
        builder.button(text="🔴 Ustaxonani Yopish", callback_data="set_status_closed")
    else:
        builder.button(text="🟢 Ustaxonani Ochish", callback_data="set_status_open")
    
    txt = "🟢 Ochiq (Mijozlar buyurtma bera oladi)" if status == "open" else "🔴 Yopiq (Buyurtmalar to'xtatilgan)"
    await message.answer(f"🛠 <b>Ustaxona holati:</b> {txt}", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("set_status_"))
async def change_status(call: CallbackQuery):
    new_status = call.data.split("_")[2]
    with sqlite3.connect("mebel.db") as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='status'", (new_status,))
        conn.commit()
    await call.message.edit_text(f"✅ Holat o'zgardi: {'🟢 Ochiq' if new_status == 'open' else '🔴 Yopiq'}")
    await call.answer()

# ================= 8. ISHGA TUSHIRISH =================
async def main():
    init_db()
    dp.include_router(router)
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
