from aiogram.client.default import DefaultBotProperties
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ================= SOZLAMALAR =================
BOT_TOKEN = "8919365987:AAGrk40jcCBExtEj8_vDQhwk6OV8xzwpXYo"
# O'zingizning Telegram ID raqamingizni yozing (Barcha huquqlarga ega asosiy admin)
MAIN_ADMIN_ID = 8488028783

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= BAZA (SQLite) =================
def init_db():
    conn = sqlite3.connect("mebel.db")
    cur = conn.cursor()
    # Xodimlar (Admin yoki Usta)
    cur.execute("""CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, role TEXT)""")
    # Bo'limlar
    cur.execute("""CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)""")
    # Mahsulotlar
    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        cat_id INTEGER, name TEXT, desc TEXT, price TEXT, photo_id TEXT
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

# ================= HOLATLAR (FSM) =================
class AddCategory(StatesGroup):
    name = State()

class AddProduct(StatesGroup):
    cat_id = State()
    photo = State()
    name = State()
    desc = State()
    price = State()

class AddStaff(StatesGroup):
    user_id = State()
    role = State()

# ================= TUGMALAR =================
def user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛋 Katalog"), KeyboardButton(text="🛒 Savatcha")],
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
        keyboard=[
            [KeyboardButton(text="📥 Yangi buyurtmalar"), KeyboardButton(text="🛠 Jarayondagi ishlar")],
            [KeyboardButton(text="✅ Tayyor bo'lganlar")]
        ], resize_keyboard=True
    )

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True
    )

# ================= ASOSIY BUYRUQLAR =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    fname = message.from_user.first_name

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

# ================= ADMIN: BO'LIM QO'SHISH =================
@router.message(F.text == "📂 Bo'lim qo'shish")
async def add_cat_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Yangi bo'lim nomini yozing (masalan: Oshxona mebellari):", reply_markup=cancel_keyboard())
    await state.set_state(AddCategory.name)

@router.message(AddCategory.name)
async def add_cat_save(message: Message, state: FSMContext):
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT INTO categories (name) VALUES (?)", (message.text,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ <b>{message.text}</b> bo'limi qo'shildi!", reply_markup=admin_keyboard())
    await state.clear()

# ================= ADMIN: MAHSULOT QO'SHISH =================
@router.message(F.text == "➕ Mahsulot qo'shish")
async def add_prod_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    
    conn = sqlite3.connect("mebel.db")
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()

    if not cats:
        await message.answer("Avval '📂 Bo'lim qo'shish' tugmasi orqali bo'lim yarating!")
        return

    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=cat[1], callback_data=f"addprod_{cat[0]}")
    builder.adjust(2) # 2 qator qilib chiroyli chiqarish

    await message.answer("Qaysi bo'limga mahsulot qo'shamiz?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("addprod_"))
async def add_prod_photo(call: CallbackQuery, state: FSMContext):
    cat_id = call.data.split("_")[1]
    await state.update_data(cat_id=cat_id)
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
    await message.answer("Mahsulot haqida ma'lumot (o'lchami, materiali):")
    await state.set_state(AddProduct.desc)

@router.message(AddProduct.desc)
async def add_prod_price(message: Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("Narxini kiriting (masalan: 3 000 000 so'm):")
    await state.set_state(AddProduct.price)

@router.message(AddProduct.price)
async def add_prod_save(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect("mebel.db")
    conn.execute(
        "INSERT INTO products (cat_id, name, desc, price, photo_id) VALUES (?, ?, ?, ?, ?)",
        (data['cat_id'], data['name'], data['desc'], message.text, data['photo'])
    )
    conn.commit()
    conn.close()

    await message.answer_photo(
        photo=data['photo'],
        caption=f"✅ <b>Saqlandi!</b>\n\n🛋 {data['name']}\n📝 {data['desc']}\n💰 Narxi: {message.text}",
        reply_markup=admin_keyboard()
    )
    await state.clear()

# ================= MIJOZ: KATALOG VA MAHSULOTLAR (2/3 QATORLI) =================
@router.message(F.text == "🛋 Katalog")
async def show_catalog(message: Message):
    conn = sqlite3.connect("mebel.db")
    cats = conn.execute("SELECT id, name FROM categories").fetchall()
    conn.close()

    if not cats:
        await message.answer("Katalog hozircha bo'sh.")
        return

    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=cat[1], callback_data=f"showcat_{cat[0]}")
    builder.adjust(2) # Bo'limlar 2 qatordan chiqadi

    await message.answer("Barcha bo'limlar:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("showcat_"))
async def show_products(call: CallbackQuery):
    cat_id = call.data.split("_")[1]
    conn = sqlite3.connect("mebel.db")
    prods = conn.execute("SELECT name, desc, price, photo_id FROM products WHERE cat_id=?", (cat_id,)).fetchall()
    conn.close()

    if not prods:
        await call.answer("Bu bo'limda hozircha mahsulot yo'q", show_alert=True)
        return
    
    await call.message.delete()
    for p in prods:
        caption = f"🛋 <b>{p[0]}</b>\n\n📝 {p[1]}\n\n💰 <b>Narxi:</b> {p[2]}"
        # Savatga qo'shish tugmasi
        builder = InlineKeyboardBuilder()
        builder.button(text="🛒 Savatga qo'shish", callback_data="buy_item")
        
        await call.message.answer_photo(photo=p[3], caption=caption, reply_markup=builder.as_markup())

# ================= ADMIN: XODIM/USTA QO'SHISH =================
@router.message(F.text == "👥 Xodim qo'shish")
async def add_staff_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Yangi xodimning Telegram ID raqamini jo'nating:", reply_markup=cancel_keyboard())
    await state.set_state(AddStaff.user_id)

@router.message(AddStaff.user_id)
async def add_staff_role(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("ID faqat raqamlardan iborat bo'lishi kerak!")
        return
    
    await state.update_data(user_id=int(message.text))
    builder = ReplyKeyboardBuilder()
    builder.button(text="Usta")
    builder.button(text="Admin")
    builder.button(text="❌ Bekor qilish")
    builder.adjust(2)
    
    await message.answer("Kim bo'lib ishlaydi? Rolni tanlang:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(AddStaff.role)

@router.message(AddStaff.role)
async def add_staff_save(message: Message, state: FSMContext):
    role = message.text.lower()
    if role not in ["usta", "admin"]: return
    
    data = await state.get_data()
    conn = sqlite3.connect("mebel.db")
    conn.execute("INSERT OR REPLACE INTO staff (user_id, role) VALUES (?, ?)", (data['user_id'], role))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Yaxshi! ID: {data['user_id']} botda <b>{role}</b> huquqini oldi.", reply_markup=admin_keyboard())
    await state.clear()

# ================= ASOSIY FUNKSIYA =================
async def main():
    init_db() # Bazani yaratish
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
