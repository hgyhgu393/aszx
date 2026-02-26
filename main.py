import discord
from discord.ext import tasks, commands
from discord import app_commands
import os
import json
import requests
import asyncio
from datetime import datetime
from flask import Flask
from threading import Thread

# --- [ 1. ระบบเปิดประตูหน้าบ้าน (Flask) เพื่อกันบอทดับ ] ---
app = Flask('')

@app.route('/')
def home():
    return "Uptime Monitor Bot is Running! 🟢"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# --- [ 2. ตั้งค่าบอทและดึงค่าจากระบบ ] ---
TOKEN = os.getenv('BOT_TOKEN')
DATABASE_CHANNEL_ID = int(os.getenv('DB_CHANNEL', 0))

# ฐานข้อมูลชั่วคราวใน RAM
user_data = {}  # { "user_id": ["url1", "url2"] }
status_logs = {} # { "url": "log message" }

class UptimeSystemView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ปุ่มที่ 1: เพิ่มลิงก์
    @discord.ui.button(label="➕ เพิ่มลิงก์ (สูงสุด 5)", style=discord.ButtonStyle.primary, custom_id="add_btn")
    async def add_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        if uid not in user_data: user_data[uid] = []
        if len(user_data[uid]) >= 5:
            return await interaction.response.send_message("❌ คุณเพิ่มลิงก์ครบ 5 ลิงก์แล้ว!", ephemeral=True)
        await interaction.response.send_modal(AddLinkModal(uid))

    # ปุ่มที่ 2: เหตุการณ์ล่าสุด (Logs)
    @discord.ui.button(label="🔔 เหตุการณ์ล่าสุด", style=discord.ButtonStyle.secondary, custom_id="log_btn")
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        urls = user_data.get(uid, [])
        if not urls: return await interaction.response.send_message("คุณยังไม่มีลิงก์ในระบบ", ephemeral=True)
        
        embed = discord.Embed(title="📜 รายงานเหตุการณ์แบบ Real-time", color=0xffa500)
        for url in urls:
            log = status_logs.get(url, "⏳ กำลังรอการตรวจสอบ...")
            embed.add_field(name=f"🔗 {url}", value=log, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ปุ่มที่ 3: ดูข้อมูล Real-time (แสดงสีเขียว/แดง)
    @discord.ui.button(label="📊 ดูข้อมูล Real-time", style=discord.ButtonStyle.success, custom_id="status_btn")
    async def view_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        urls = user_data.get(uid, [])
        if not urls: return await interaction.response.send_message("คุณยังไม่มีลิงก์ในระบบ", ephemeral=True)
        
        view = discord.ui.View()
        for url in urls:
            view.add_item(StatusDetailButton(url))
        await interaction.response.send_message("เลือก URL ที่ต้องการดูสถานะสด:", view=view, ephemeral=True)

# --- [ Modal สำหรับกรอก URL ] ---
class AddLinkModal(discord.ui.Modal, title='เพิ่มลิงก์เข้าสู่ระบบ'):
    url_input = discord.ui.TextInput(label='กรอก URL (ต้องขึ้นต้นด้วย http)', placeholder='https://my-bot.onrender.com')

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value
        if not url.startswith("http"):
            return await interaction.response.send_message("❌ URL ไม่ถูกต้อง!", ephemeral=True)
        
        user_data[self.uid].append(url)
        status_logs[url] = "Online 🟢 (เพิ่งเริ่ม)"
        await bot.save_to_db()
        await interaction.response.send_message(f"✅ เพิ่มลิงก์ `{url}` เรียบร้อย!", ephemeral=True)

# --- [ ปุ่มเลือกดูสถานะรายตัว ] ---
class StatusDetailButton(discord.ui.Button):
    def __init__(self, url):
        super().__init__(label=url, style=discord.ButtonStyle.gray)
        self.url = url

    async def callback(self, interaction: discord.Interaction):
        status = status_logs.get(self.url, "Offline")
        is_online = "Online" in status
        color = discord.Color.green() if is_online else discord.Color.red()
        emoji = "🟢 เขียว (ระบบออนไลน์)" if is_online else "🔴 แดง (ระบบล่ม/หลับ)"
        
        embed = discord.Embed(title="📈 สถานะข้อมูล Real-time", color=color)
        embed.add_field(name="เป้าหมาย", value=self.url, inline=False)
        embed.add_field(name="สถานะปัจจุบัน", value=emoji, inline=True)
        embed.set_footer(text=f"อัปเดตเมื่อ: {datetime.now().strftime('%H:%M:%S')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- [ ตัวบอทหลัก ] ---
class MonitorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(UptimeSystemView())
        self.load_db.start()
        self.auto_ping_task.start()
        await self.tree.sync()

    @tasks.loop(count=1)
    async def load_db(self):
        await self.wait_until_ready()
        channel = self.get_channel(DATABASE_CHANNEL_ID)
        if channel:
            async for msg in channel.history(limit=1):
                try:
                    global user_data
                    user_data = json.loads(msg.content)
                except: pass

    async def save_to_db(self):
        channel = self.get_channel(DATABASE_CHANNEL_ID)
        if channel:
            await channel.purge(limit=1)
            await channel.send(json.dumps(user_data))

    @tasks.loop(minutes=1)
    async def auto_ping_task(self):
        all_urls = set()
        for urls in user_data.values(): all_urls.update(urls)
        for url in all_urls:
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    status_logs[url] = f"Online 🟢 (ปกติ) - {datetime.now().strftime('%H:%M')}"
                else:
                    status_logs[url] = f"Error ⚠️ (Code: {res.status_code})"
            except:
                status_logs[url] = "Offline 🔴 (ล่ม/เชื่อมต่อไม่ได้)"

bot = MonitorBot()

@bot.tree.command(name="setup", description="ติดตั้งแผงควบคุมลงในห้องนี้")
@app_commands.describe(image_url="ลิงก์รูปภาพหน้าปก", channel="เลือกห้องที่จะส่ง")
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, image_url: str = None):
    embed = discord.Embed(
        title="🛰️ ระบบเชื่อมต่อบอทและตรวจสอบสถานะ",
        description="ยินดีต้อนรับ! ใช้ปุ่มด้านล่างเพื่อจัดการบอทของคุณ\n\n1️⃣ เพิ่มลิงก์บอทเพื่อให้ระบบช่วย 'สะกิด' ไม่ให้หลับ\n2️⃣ ตรวจดูเหตุการณ์ล่มหรือข้อผิดพลาดแบบสดๆ\n3️⃣ ดูสถานะสีเขียว/แดงแบบรายตัว",
        color=discord.Color.blue()
    )
    if image_url: embed.set_image(url=image_url)
    
    await channel.send(embed=embed, view=UptimeSystemView())
    await interaction.response.send_message(f"✅ ติดตั้งแผงควบคุมที่ห้อง {channel.mention} เรียบร้อย!", ephemeral=True)

if __name__ == "__main__":
    keep_alive() # เปิดประตู Flask
    bot.run(TOKEN)
    
