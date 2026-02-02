import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    MessageHandler, filters, ConversationHandler
)
from fpdf import FPDF
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask
from threading import Thread
from supabase import create_client, Client

# 1. SETUP LOGGING & ENV
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
load_dotenv()

TOKEN = os.getenv('TELEGRAM_TOKEN')
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Inisialisasi Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Definisi State
NAMA, GMAIL, BUDGET, TANGGAL_H, NAMA_BARANG, PENGELUARAN = range(6)

# --- FUNGSI HELPER DATABASE ---
def get_user_profile(user_id):
    res = supabase.table("profiles").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

def get_today_expenses(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    res = supabase.table("pengeluaran").select("*").eq("user_id", user_id).gte("created_at", today).execute()
    return res.data

# --- HANDLERS REGISTRASI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Berikan Nama")
    return NAMA

async def ambil_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nama'] = update.message.text
    await update.message.reply_text(f"Oke {update.message.text}, masukin alamat Gmail:")
    return GMAIL

async def ambil_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    nama = context.user_data['nama']
    gmail = update.message.text
    
    # Simpan/Update ke Supabase
    supabase.table("Daily_Raports").upsert({"user_id": user_id, "nama": nama, "gmail": gmail}).execute()
    await update.message.reply_text("Profil telah tersimpan. Ketik /newmonth buat set budget bulan ini.")
    return ConversationHandler.END

# --- HANDLERS BUDGET ---
async def new_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Berapa budget jajan lu bulan ini?")
    return BUDGET

async def ambil_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    budget = int(update.message.text)
    
    # Update Budget di database
    supabase.table("profiles").update({"budget": budget}).eq("user_id", user_id).execute()
    
    await update.message.reply_text(f"Budget Rp{budget:,} sudah tersimpan! jika ada pengeluaran ketik /pengeluaran.")
    return ConversationHandler.END

# --- HANDLERS JAJAN ---
async def pengeluaran_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Masukkan nama pembelian")
    return NAMA_BARANG

async def ambil_nama_barang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_item'] = update.message.text
    await update.message.reply_text(f"Berapa harga {update.message.text}?")
    return PENGELUARAN

async def ambil_pengeluaran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    item = context.user_data.get('temp_item')
    harga = int(update.message.text)
    
    # 1. Simpan ke Tabel Pengeluaran
    supabase.table("pengeluaran").insert({"user_id": user_id, "item": item, "harga": harga}).execute()
    
    # 2. Hitung Sisa Budget Real-time (Dari Database)
    profile = get_user_profile(user_id)
    all_expenses = supabase.table("pengeluaran").select("harga").eq("user_id", user_id).execute()
    total_spent = sum(x['harga'] for x in all_expenses.data)
    sisa = (profile['budget'] if profile else 0) - total_spent
    
    await update.message.reply_text(
        f"✅ {item} (Rp{harga:,}) dicatat!\n"
        f"Sisa Budget Bulan ini : Rp{sisa:,}\n\n"
        "Ketik /pengeluaran lagi atau /cetak_pdf."
    )
    return ConversationHandler.END

# --- FUNGSI PDF (STOCKBIT LOOK) ---
def generate_pdf_stockbit(nama, gmail, list_jajan, budget_awal):
    pdf = FPDF()
    pdf.add_page()
    
    # Header
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Trade Confirmation", ln=True)
    pdf.set_font("Arial", size=9)
    pdf.cell(0, 5, f"Client: {nama.upper()}", ln=True)
    pdf.cell(0, 5, f"Date: {datetime.now().strftime('%d/%m/%Y')}", ln=True)
    pdf.ln(10)
    
    # Table Header
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(110, 8, " ITEM DESCRIPTION", border=1, fill=True)
    pdf.cell(80, 8, " AMOUNT (IDR)", border=1, ln=True, fill=True, align='R')
    
    # Table Content
    pdf.set_font("Arial", size=10)
    total_today = 0
    for jajan in list_jajan:
        pdf.cell(110, 8, f" {jajan['item'].upper()}", border=1)
        pdf.cell(80, 8, f"{jajan['harga']:,} ", border=1, ln=True, align='R')
        total_today += jajan['harga']
    
    # Summary
    pdf.ln(5)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(110, 10, "TOTAL EXPENDITURE TODAY", align='R')
    pdf.cell(80, 10, f" Rp {total_today:,} ", ln=True, align='R')
    
    filename = f"Trade_Conf_{nama}.pdf"
    pdf.output(filename)
    return filename

async def cetak_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    profile = get_user_profile(user_id)
    list_jajan = get_today_expenses(user_id)
    
    if not list_jajan:
        await update.message.reply_text("Belum ada pengeluaran.")
        return

    file_pdf = generate_pdf_stockbit(profile['nama'], profile['gmail'], list_jajan, profile['budget'])
    
    # Kirim ke Telegram
    await update.message.reply_document(document=open(file_pdf, 'rb'), caption="Laporan Harian")
    
    # Kirim ke Email
    kirim_email_laporan(profile['gmail'], file_pdf, profile['nama'])

def kirim_email_laporan(ke_email, file_pdf, nama_user):
    pengirim = os.getenv('GMAIL_USER')
    password = os.getenv('GMAIL_PASSWORD')
    try:
        pesan = MIMEMultipart()
        pesan['From'], pesan['To'], pesan['Subject'] = pengirim, ke_email, f"Financial Report - {nama_user}"
        pesan.attach(MIMEText("Terlampir laporan transaksi harian anda.", 'plain'))
        with open(file_pdf, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={file_pdf}")
        pesan.attach(part)
        
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(pengirim, password)
            server.send_message(pesan)
        return True
    except Exception as e:
        print(f"Error Email: {e}")
        return False

# --- WEB SERVER FOR RENDER ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"

def run_flask(): app.run(host='0.0.0.0', port=8080)

if __name__ == '__main__':
    Thread(target=run_flask).start()
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('newmonth', new_month),
            CommandHandler('pengeluaran', pengeluaran_start)
        ],
        states={
            NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_nama)],
            GMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_gmail)],
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_budget)],
            NAMA_BARANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_nama_barang)],
            PENGELUARAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_pengeluaran)],
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('cetak_pdf', cetak_manual))
    
    print("Bot is running...")
    application.run_polling()