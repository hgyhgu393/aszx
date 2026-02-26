import discord
from discord.ext import tasks, commands
from discord import app_commands
import os
import requests
import asyncio
import json
from datetime import datetime
from flask import Flask
from threading import Thread

# --- [ 1. ระบบเปิดประตู Port 8080 เพื่อกันบอทดับ ] ---
app = Flask('')

@app.route('/')
def home():
    return "<h1>Uptime Bot is Online!</h1><p>Ready to receive ping requests.</p>"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """ฟังก์ชันเปิดประตูหน้าบ้านให้เว็บภายนอกยิงเข้ามาปลุกบอท"""
    t = Thread(target=run_web)
    t.start()

# --- [ 2. ตั้งค่าบอทและตัวแปร ] ---
# ดึง Token จาก Environment Variable (ปลอดภัยที่สุดสำหรับ GitHub)
TOKEN = os.getenv('BOT_TOKEN')

# ฐานข้อมูลเก็บใน RAM (หมายเหตุ: ถ้าบอทรีสตาร์ทบน Render ข้อมูลจะรีเซ็ต 
# แนะนำให้ก๊อปปี้ลิงก์ใส่ใหม่หรือใช้วิธี Environment Variable เก็บแทน)
user_data = {}  # เก็บ URL แยกตาม User ID
status_logs = {} # เก็บเหตุการณ์ Error/Online

class UptimeSystemView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 1. ปุ่มเพิ่มลิงก์
    @discord.ui.button(label="➕ เพิ่มลิงก์ (สูงสุด 5)", style=discord.ButtonStyle.primary, custom_id="add_btn")
    async def add_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        if uid not in user_data: user_data[uid] = []
        if len(user_data[uid]) >= 5:
            return await interaction.response.send_message("❌ คุณมีลิ้งก์ครบ 5 แล้ว!", ephemeral=True)
        await interaction.response.send_modal(AddLinkModal(uid))

    # 2. ปุ่มเหตุการณ์ (Logs แบบ Real-time)
    @discord.ui.button(label="🔔 เหตุการณ์ล่าสุด", style=discord.ButtonStyle.secondary, custom_id="log_btn")
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        urls = user_data.get(uid, [])
        if not urls: return await interaction.response.send_message("ไม่มีลิ้งก์ในระบบ", ephemeral=True)
        
        embed = discord.Embed(title="📜 เหตุการณ์ (Real-time Logs)", color=0xffa500)
        for url in urls:
            log = status_logs.get(url, "⏳ กำลังประมวลผล...")
            embed.add_field(name=f"🔗 {url}", value=log, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 3. ปุ่มดูข้อมูล (แสดงสถานะสีเขียว/แดง)
    @discord.ui.button(label="📊 ดูข้อมูล Real-time", style=discord.ButtonStyle.success, custom_id="status_btn")
    async def view_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        urls = user_data.get(uid, [])
        if not urls: return await interaction.response.send_message("ไม่มีลิ้งก์ในระบบ", ephemeral=True)
        
        # สร้างเมนูเลือก URL ที่จะดู
        view = discord.ui.View()
        for url in urls:
            view.add_item(StatusDetailButton(url))
        await interaction.response.send_message("เลือก URL ที่ต้องการดูสถานะสด:", view=view, ephemeral=True)

# --- [ Modal สำหรับกรอกลิงก์ ] ---
class AddLinkModal(discord.ui.Modal, title='เพิ่มลิ้งก์บอทของคุณ'):
    url_input = discord.ui.TextInput(label='กรอก URL', placeholder='https://my-bot.onrender.com')

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value
        if not url.startswith("http"):
            return await interaction.response.send_message("URL ผิดพลาด!", ephemeral=True)
        
        user_data[self.uid].append(url)
        status_logs[url] = "Online 🟢 (System Starting)"
        await interaction.response.send_message(f"✅ เพิ่ม `{url}` สำเร็จ!", ephemeral=True)

# --- [ ปุ่มย่อยดูสถานะรายตัว ] ---
class StatusDetailButton(discord.ui.Button):
    def __init__(self, url):
        super().__init__(label=url, style=discord.ButtonStyle.gray)
        self.url = url

    async def callback(self, interaction: discord.Interaction):
        log = status_logs.get(self.url, "")
        is_online = "Online" in log
        
        color = discord.Color.green() if is_online else discord.Color.red()
        emoji = "🟢 เขียว (ทำงานปกติ)" if is_online else "🔴 แดง (ล่ม/ดับ)"
        
        embed = discord.Embed(title="📈 Live Status Check", color=color)
        embed.add_field(name="เป้าหมาย", value=self.url, inline=False)
        embed.add_field(name="ผลการตรวจสอบ", value=emoji, inline=True)
        embed.set_footer(text=f"Check time: {datetime.now().strftime('%H:%M:%S')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- [ ตัวบอทหลัก ] ---
class MonitorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(UptimeSystemView())
        self.auto_ping_task.start()
        await self.tree.sync()

    @tasks.loop(minutes=1)
    async def auto_ping_task(self):
        """ระบบกระตุ้นลิงก์ทุกๆ 1 นาที (กันบอทหลับ)"""
        all_urls = []
        for urls in user_data.values():
            all_urls.extend(urls)
        
        for url in list(set(all_urls)): # ป้องกันการ Ping ซ้ำ
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    status_logs[url] = f"Online 🟢 (200 OK) - {datetime.now().strftime('%H:%M')}"
                else:
                    status_logs[url] = f"Error ⚠️ (Code: {res.status_code})"
            except:
                status_logs[url] = "Offline 🔴 (Connection Timeout)"

    async def on_ready(self):
        print(f'✅ บอทออนไลน์แล้ว: {self.user}')

bot = MonitorBot()

# --- [ คำสั่ง /setup ] ---
@bot.tree.command(name="setup", description="ตั้งค่าแผงควบคุม UI")
@app_commands.describe(channel="เลือกห้องที่จะส่ง", image_url="ลิ้งก์รูปหน้าปก")
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, image_url: str = None):
    embed = discord.Embed(
        title="🛰️ Uptime Monitor & Connection Bot",
        description="คลิกปุ่มด้านล่างเพื่อจัดการและดูสถานะบอทของคุณแบบ Real-time",
        color=discord.Color.blue()
    )
    if image_url: embed.set_image(url=image_url)
    
    await channel.send(embed=embed, view=UptimeSystemView())
    await interaction.response.send_message(f"ส่ง UI ไปยังห้อง {channel.mention} แล้ว", ephemeral=True)

if __name__ == "__main__":
    keep_alive() # เปิดประตู Flask (Port 8080)
    bot.run(TOKEN)
    
