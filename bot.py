import os
import logging
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    ConversationHandler
)
from fpdf import FPDF
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from flask import Flask
from threading import Thread

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

# Definisi State
NAMA, GMAIL, BUDGET, TANGGAL_H, PENGELUARAN, NAMA_BARANG = range(6)

# --- FUNGSI REGISTRASI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Berikan Nama Anda:")
    return NAMA

async def ambil_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nama'] = update.message.text
    await update.message.reply_text(f"Halo {update.message.text}, Masukkan Gmail Anda:")
    return GMAIL

async def ambil_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['gmail'] = update.message.text
    await update.message.reply_text("Gmail Berhasil Disimpan! Ketik /newmonth buat setting budget.")
    return ConversationHandler.END

# --- FUNGSI BUDGET ---
async def new_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Masukkan Budget Bulanan (Angka saja):")
    return BUDGET

async def ambil_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text.isdigit():
        await update.message.reply_text("Masukkan angka saja tanpa simbol lain!")
        return BUDGET
    context.user_data['budget'] = int(text)
    await update.message.reply_text("Bulan ini ada berapa hari? (28-31)")
    return TANGGAL_H

async def ambil_tanggal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tanggal = update.message.text
    if not tanggal.isdigit() or not (1 <= int(tanggal) <= 31):
        await update.message.reply_text("Masukkan tanggal yang valid!")
        return TANGGAL_H
    context.user_data['hari_h'] = int(tanggal)
    budget = context.user_data['budget']
    await update.message.reply_text(f"Budget Rp{budget:,} & Tanggal {tanggal} tersimpan.\nKetik /pengeluaran tiap kali jajan.")
    return ConversationHandler.END

# --- FUNGSI JAJAN (ALA STOCKBIT) ---
async def pengeluaran_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Beli apa le? (Nama Barang)")
    return NAMA_BARANG

async def ambil_nama_barang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_item'] = update.message.text
    await update.message.reply_text(f"Berapa harga {update.message.text}?")
    return PENGELUARAN

async def ambil_pengeluaran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    harga = update.message.text
    if not harga.isdigit():
        await update.message.reply_text("Input angka!")
        return PENGELUARAN
    
    nominal = int(harga)
    nama_barang = context.user_data.get('temp_item')
    
    # Perbaikan Typo List
    if 'list_jajan' not in context.user_data:
        context.user_data['list_jajan'] = []
    
    # Simpan ke list
    context.user_data['list_jajan'].append({
        'tanggal': datetime.now().strftime("%d/%m/%Y"),
        'item': nama_barang,
        'harga': nominal
    })

    # Update Budget
    sisa = context.user_data.get('budget', 0) - nominal
    context.user_data['budget'] = sisa
    
    await update.message.reply_text(
        f"✅ {nama_barang} (Rp{nominal:,}) dicatat!\n"
        f"Sisa Budget: Rp{sisa:,}\n\n"
        "Ketik /pengeluaran lagi atau /cetak_pdf buat dapet laporan."
    )
    return ConversationHandler.END

# --- FUNGSI PDF & EMAIL ---
def generate_pdf_stockbit(nama, gmail, list_jajan, sisa_budget):
    pdf = FPDF()
    pdf.add_page()
    
    # Header Trade Confirmation
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 10, "Trade Confirmation", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 10, f"Transaction Date: {datetime.now().strftime('%d/%m/%Y')}", ln=True, align='R')
    pdf.ln(5)
    
    pdf.cell(0, 5, f"To: {nama.upper()}", ln=True)
    pdf.cell(0, 5, f"Email: {gmail}", ln=True)
    pdf.ln(10)
    
    # Tabel
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(100, 10, " NAMA BARANG", border=1, fill=True)
    pdf.cell(90, 10, " NOMINAL", border=1, ln=True, fill=True, align='R')
    
    pdf.set_font("Arial", size=11)
    for jajan in list_jajan:
        pdf.cell(100, 10, f" {jajan['item']}", border=1)
        pdf.cell(90, 10, f" Rp{jajan['harga']:,} ", border=1, ln=True, align='R')
    
    pdf.ln(5)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(100, 10, " SISA SALDO", border=0)
    pdf.cell(90, 10, f" Rp{sisa_budget:,} ", border=0, ln=True, align='R')
    
    filename = f"Trade_Conf_{nama}.pdf"
    pdf.output(filename)
    return filename

async def cetak_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'list_jajan' not in context.user_data or not context.user_data['list_jajan']:
        await update.message.reply_text("Belum ada data pengeluaran.")
        return

    nama = context.user_data.get('nama', 'User')
    gmail = context.user_data.get('gmail', 'Unknown')
    sisa = context.user_data.get('budget', 0)
    list_jajan = context.user_data['list_jajan']

    file_pdf = generate_pdf_stockbit(nama, gmail, list_jajan, sisa)
    await update.message.reply_document(document=open(file_pdf, 'rb'), caption="Ini Trade Confirmation lu hari ini le!")
    
    # Fungsi kirim email (tetap pake smtplib lu yang lama)
    kirim_email_laporan(gmail, file_pdf, nama)

def kirim_email_laporan(ke_email, file_pdf, nama_user):
    pengirim = os.getenv('GMAIL_USER')
    password = os.getenv('GMAIL_PASSWORD')
    # ... (logika smtplib lu yang lama udah bener, pastiin aja panggilannya smtp.gmail.com) ...
    try:
        pesan = MIMEMultipart()
        pesan['From'], pesan['To'], pesan['Subject'] = pengirim, ke_email, f"Laporan {nama_user}"
        pesan.attach(MIMEText("Laporan harian terlampir.", 'plain'))
        with open(file_pdf, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={file_pdf}")
        pesan.attach(part)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(pengirim, password)
        server.send_message(pesan)
        server.quit()
        return True
    except: return False

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Input dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

if __name__ == '__main__':
    keep_alive()
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
            TANGGAL_H: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_tanggal)],
            NAMA_BARANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_nama_barang)], # PERBAIKAN: Ditambahin
            PENGELUARAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_pengeluaran)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('cetak_pdf', cetak_manual)) # Tombol Cetak
    
    print("Bot aktif")
    application.run_polling()