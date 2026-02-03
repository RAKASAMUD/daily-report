import os
import logging
from datetime import datetime, time
import pytz
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
NAMA, GMAIL, BUDGET, TANGGAL_H, NAMA_BARANG, PENGELUARAN, SET_JAM = range(7)

# --- DATABASE HELPERS ---
def get_user_profile(user_id):
    res = supabase.table("Daily_Raport").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

def get_today_expenses(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    res = supabase.table("pengeluaran").select("*").eq("user_id", user_id).gte("created_at", today).execute()
    return res.data

def get_total_spent(user_id):
    res = supabase.table("pengeluaran").select("harga").eq("user_id", user_id).execute()
    return sum(x['harga'] for x in res.data)

# --- REGISTRASI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Masukkan nama Anda:")
    return NAMA

async def ambil_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nama'] = update.message.text
    await update.message.reply_text("Masukkan alamat Gmail:")
    return GMAIL

async def ambil_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    nama, gmail = context.user_data['nama'], update.message.text
    supabase.table("Daily_Raport").upsert({"user_id": user_id, "nama": nama, "gmail": gmail}).execute()
    await update.message.reply_text("Profil sudah disimpan. Ketik /newmonth untuk set budget.")
    return ConversationHandler.END

# --- BUDGET ---
async def new_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Berapa budget bulan ini?")
    return BUDGET

async def ambil_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, budget = update.message.from_user.id, int(update.message.text)
    supabase.table("Daily_Raport").update({"budget": budget}).eq("user_id", user_id).execute()
    await update.message.reply_text(f"Budget Rp{budget:,} disimpan. Ketik /pengeluaran untuk mencatat.")
    return ConversationHandler.END

# --- JADWAL LAPORAN ---
async def set_jam_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Set jam pengiriman laporan (Contoh 21:00):")
    return SET_JAM

async def simpan_jam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, jam = update.message.from_user.id, update.message.text
    try:
        if ":" not in jam: raise ValueError
        supabase.table("Daily_Raport").update({"report_time": jam}).eq("user_id", user_id).execute()
        await update_job_timer(user_id, jam, context)
        await update.message.reply_text(f"Laporan akan dikirim setiap jam {jam} WIB.")
        return ConversationHandler.END
    except:
        await update.message.reply_text("Format salah. Gunakan HH:MM (Contoh 21:00):")
        return SET_JAM

# --- PENGELUARAN ---
async def pengeluaran_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Nama pengeluaran:")
    return NAMA_BARANG

async def ambil_nama_barang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_item'] = update.message.text
    await update.message.reply_text(f"Harga {update.message.text}:")
    return PENGELUARAN

async def ambil_pengeluaran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, item, harga = update.message.from_user.id, context.user_data.get('temp_item'), int(update.message.text)
    supabase.table("pengeluaran").insert({"user_id": user_id, "item": item, "harga": harga}).execute()
    profile = get_user_profile(user_id)
    sisa = (profile['budget'] if profile else 0) - get_total_spent(user_id)
    await update.message.reply_text(f"✅ {item} (Rp{harga:,}) dicatat. Sisa: Rp{sisa:,}")
    return ConversationHandler.END

# --- PDF & EMAIL ---
def generate_pdf_stockbit(nama, list_jajan, sisa_real, user_id):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.cell(0, 10, "Daily Expense Report", ln=True)
    pdf.set_font("Arial", size=9); pdf.cell(0, 5, f"Client: {nama.upper()}", ln=True)
    pdf.cell(0, 5, f"Date: {datetime.now().strftime('%d/%m/%Y')}", ln=True); pdf.ln(10)
    pdf.set_fill_color(240, 240, 240); pdf.set_font("Arial", "B", 10)
    pdf.cell(110, 8, " ITEM", border=1, fill=True); pdf.cell(80, 8, " AMOUNT", border=1, ln=True, fill=True, align='R')
    pdf.set_font("Arial", size=10); total_today = 0
    for j in list_jajan:
        pdf.cell(110, 8, f" {j['item'].upper()}", border=1); pdf.cell(80, 8, f"{j['harga']:,} ", border=1, ln=True, align='R')
        total_today += j['harga']
    pdf.ln(5); pdf.set_font("Arial", "B", 11)
    pdf.cell(110, 10, "TOTAL TODAY", align='R'); pdf.cell(80, 10, f" Rp {total_today:,} ", border=1, ln=True, align='R')
    pdf.set_fill_color(200, 255, 200); pdf.cell(110, 10, "REMAINING BUDGET", align='R'); pdf.cell(80, 10, f" Rp {sisa_real:,} ", border=1, ln=True, align='R', fill=True)
    filename = f"Report_{user_id}.pdf"; pdf.output(filename); return filename

async def cetak_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    profile, list_jajan = get_user_profile(user_id), get_today_expenses(user_id)
    if not list_jajan: return await update.message.reply_text("Data hari ini kosong.")
    sisa = profile['budget'] - get_total_spent(user_id)
    file_pdf = generate_pdf_stockbit(profile['nama'], list_jajan, sisa, user_id)
    await update.message.reply_document(document=open(file_pdf, 'rb'), caption="Laporan harian Anda.")
    kirim_email_laporan(profile['gmail'], file_pdf, profile['nama'])

def kirim_email_laporan(ke_email, file_pdf, nama_user):
    pengirim, password = os.getenv('GMAIL_USER'), os.getenv('GMAIL_PASSWORD')
    try:
        pesan = MIMEMultipart(); pesan['From'], pesan['To'], pesan['Subject'] = pengirim, ke_email, f"Report - {nama_user}"
        pesan.attach(MIMEText("Laporan transaksi harian.", 'plain'))
        with open(file_pdf, "rb") as f:
            part = MIMEBase("application", "octet-stream"); part.set_payload(f.read())
        encoders.encode_base64(part); part.add_header("Content-Disposition", f"attachment; filename={file_pdf}"); pesan.attach(part)
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls(); server.login(pengirim, password); server.send_message(pesan)
        return True
    except Exception as e:
        logging.error(f"Error Email: {e}"); return False

# --- SCHEDULING ---
async def send_auto_report(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    profile, list_jajan = get_user_profile(user_id), get_today_expenses(user_id)
    if not list_jajan: return
    sisa = profile['budget'] - get_total_spent(user_id)
    file_pdf = generate_pdf_stockbit(profile['nama'], list_jajan, sisa, user_id)
    await context.bot.send_document(chat_id=user_id, document=open(file_pdf, 'rb'), caption="Laporan otomatis.")
    kirim_email_laporan(profile['gmail'], file_pdf, profile['nama'])

async def update_job_timer(user_id, jam_str, context):
    current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
    for job in current_jobs: job.schedule_removal()
    hh, mm = map(int, jam_str.split(':'))
    context.job_queue.run_daily(send_auto_report, time(hour=hh, minute=mm, tzinfo=pytz.timezone('Asia/Jakarta')), name=str(user_id), user_id=user_id)

async def load_all_jobs(app):
    res = supabase.table("Daily_Raport").select("user_id, report_time").execute()
    for user in res.data:
        if user['report_time']:
            hh, mm = map(int, user['report_time'].split(':'))
            app.job_queue.run_daily(send_auto_report, time(hour=hh, minute=mm, tzinfo=pytz.timezone('Asia/Jakarta')), name=str(user['user_id']), user_id=user['user_id'])

# --- SYSTEM ---
app = Flask('')
@app.route('/')
def home(): return "Active"
def run_flask(): app.run(host='0.0.0.0', port=8080)
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Dibatalkan.", reply_markup=ReplyKeyboardRemove()); return ConversationHandler.END
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="Error:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(f"Error: <code>{context.error}</code>", parse_mode='HTML')

if __name__ == '__main__':
    Thread(target=run_flask).start()
    application = ApplicationBuilder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CommandHandler('newmonth', new_month), CommandHandler('pengeluaran', pengeluaran_start), CommandHandler('setjam', set_jam_start)],
        states={
            NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_nama)],
            GMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_gmail)],
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_budget)],
            NAMA_BARANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_nama_barang)],
            PENGELUARAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ambil_pengeluaran)],
            SET_JAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, simpan_jam)],
        },
        fallbacks=[CommandHandler('cancel', cancel_action)], allow_reentry=True
    )
    application.add_handler(conv_handler); application.add_handler(CommandHandler('cetak_pdf', cetak_manual)); application.add_error_handler(error_handler)
    async def post_init(app): await load_all_jobs(app)
    application.post_init = post_init; 
    application.run_polling()