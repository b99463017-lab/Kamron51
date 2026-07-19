import sqlite3

# O'z telegram ID raqamingizni yozing
MY_TELEGRAM_ID = 8488028783 

def make_me_admin():
    conn = sqlite3.connect('gold_mebel.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = 'admin' WHERE telegram_id = ?", (MY_TELEGRAM_ID,))
    conn.commit()
    conn.close()
    print("Siz muvaffaqiyatli admin qilindingiz!")

make_me_admin()
