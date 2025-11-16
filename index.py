from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
import random
import string
from bakong_khqr import KHQR
import qrcode
from io import BytesIO
from datetime import datetime
import pytz
import threading
import time
import base64
import requests

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"

API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkYXRhIjp7ImlkIjoiY2U3NTMwODdiMjQ5NDQzZSJ9LCJpYXQiOjE3NjE1MzU0MjgsImV4cCI6MTc2OTMxMTQyOH0.e3w8uD5"
khqr = KHQR(API_TOKEN)
BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "chhira_ly@aclb")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "855882000544")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Phnom_Penh")

users_in_payment = {}

def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            is_reseller INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_prices (
            item_id TEXT PRIMARY KEY,
            game TEXT,
            normal_price REAL,
            reseller_price REAL
        )
    """)
    conn.commit()
    conn.close()

def get_balance(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0.0

def update_balance(user_id, amount):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        new_balance = result[0] + amount
        cursor.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
    else:
        cursor.execute("INSERT INTO users(user_id,balance) VALUES(?,?)", (user_id, amount))
    conn.commit()
    conn.close()

def get_item_prices(game):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT item_id, normal_price, reseller_price FROM item_prices WHERE game=?", (game,))
    items = cursor.fetchall()
    conn.close()
    return {item[0]: {"normal": item[1], "reseller": item[2]} for item in items}

def is_reseller(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_reseller FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] == 1 if result else False

def generate_short_transaction_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_qr_code(amount):
    try:
        qr = khqr.create_qr(
            bank_account=BANK_ACCOUNT,
            merchant_name='PI YA LEGEND',
            merchant_city='Phnom Penh',
            amount=amount,
            currency='USD',
            store_label='MShop',
            phone_number=PHONE_NUMBER,
            bill_number=generate_short_transaction_id(),
            terminal_label='Cashier-01',
            static=False
        )
        qr_img = qrcode.make(qr)
        buf = BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)
        md5 = khqr.generate_md5(qr)
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        return qr_b64, md5
    except Exception as e:
        print(f"Error generating QR: {e}")
        return None, None

def check_payment_background(user_id, md5, amount):
    def check_status():
        start_time = time.time()
        while time.time() - start_time < 180:  
            try:
                url = f"https://panha-dev.vercel.app/check_payment/{md5}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data.get("success") and data.get("status") == "PAID":
                    update_balance(user_id, amount)
                    users_in_payment.pop(user_id, None)
                    print(f"User {user_id} paid ${amount:.2f}. Balance updated!")
                    break
            except requests.RequestException as e:
                print(f"Payment check API error: {e}")
            time.sleep(10)
        else:
            users_in_payment.pop(user_id, None)
            print(f"Payment not received for User {user_id} within 3 minutes.")
    threading.Thread(target=check_status).start()


@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    balance = get_balance(session['user_id'])
    return render_template("index.html", balance=balance)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user_id = int(request.form['user_id'])
        username = request.form['username']
        session['user_id'] = user_id
        session['username'] = username
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)", (user_id, username))
        conn.commit()
        conn.close()
        return redirect(url_for('home'))
    return render_template("login.html")

@app.route('/deposit', methods=['GET','POST'])
def deposit():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        amount = float(request.form['amount'])
        user_id = session['user_id']

        if user_id in users_in_payment:
            flash("You already have a pending payment. Please wait.")
            return redirect(url_for('deposit'))

        qr_b64, md5 = generate_qr_code(amount)
        if qr_b64:
            users_in_payment[user_id] = True
            check_payment_background(user_id, md5, amount)
            flash("QR Code generated! Scan to pay. Your balance will update automatically once paid.")
            return render_template("deposit.html", qr=qr_b64)
        else:
            flash("Error generating QR code. Try again.")

    return render_template("deposit.html")

@app.route('/check_payment_status/<int:user_id>')
def check_payment_status(user_id):
    paid = user_id not in users_in_payment
    return jsonify({"paid": paid})


@app.route('/game')
def game():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    ml_items = get_item_prices("MLBB")
    ff_items = get_item_prices("FF")
    return render_template("game.html", ml_items=ml_items, ff_items=ff_items, reseller=is_reseller(session['user_id']))

@app.route('/admin')
def admin():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['user_id'] != 123456:
        return "Not authorized"
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    cursor.execute("SELECT * FROM item_prices")
    items = cursor.fetchall()
    conn.close()
    return render_template("admin.html", users=users, items=items)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
