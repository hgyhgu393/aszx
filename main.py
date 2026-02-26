import discord
from discord.ext import tasks, commands
from discord import app_commands
import aiohttp
import feedparser
import json
import os
import re
import threading
from flask import Flask
from datetime import datetime

# --- [ 1. ระบบ Flask สำหรับกันดับ ] ---
app = Flask('')
@app.route('/')
def home(): return "Full System Disaster & Protection Bot Online!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- [ 2. การจัดการฐานข้อมูล ] ---
DB_FILE = 'full_config.json'

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {
        "channels": {"thai": None, "global": None, "welcome": None, "leave": None},
        "protection": {"anti_raid": False, "anti_link": False},
        "bad_words": {"enabled": False, "list": ["ควย", "เย็ด", "มึง", "กู"]},
        "subs": {}, # เก็บข้อมูลคนติดตามแจ้งเตือนใน DM
        "welcome_msg": "ยินดีต้อนรับคุณ {user}!",
        "leave_msg": "คุณ {user} ได้ออกจากเซิร์ฟเวอร์ไปแล้ว"
    }

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

# --- [ 3. ระบบ UI ปุ่มกดตั้งค่าแจ้งเตือน (ระบบเดิม) ] ---
class SettingsView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = str(user_id)
        db = load_db()
        self.settings = db["subs"].get(self.user_id, {"thai": True, "global": False})
        self.update_buttons()

    def update_buttons(self):
        self.thai_btn.style = discord.ButtonStyle.green if self.settings["thai"] else discord.ButtonStyle.grey
        self.thai_btn.label = "🇹🇭 ไทย: " + ("เปิด" if self.settings["thai"] else "ปิด")
        self.quake_btn.style = discord.ButtonStyle.green if self.settings["global"] else discord.ButtonStyle.grey
        self.quake_btn.label = "🌍 ทั่วโลก: " + ("เปิด" if self.settings["global"] else "ปิด")

    @discord.ui.button(label="🇹🇭 ไทย", custom_id="sw_thai")
    async def thai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = load_db()
        self.settings["thai"] = not self.settings["thai"]
        db["subs"][self.user_id] = self.settings
        save_db(db)
        self.update_buttons()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🌍 ทั่วโลก", custom_id="sw_global")
    async def quake_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = load_db()
        self.settings["global"] = not self.settings["global"]
        db["subs"][self.user_id] = self.settings
        save_db(db)
        self.update_buttons()
        await interaction.response.edit_message(view=self)

class AlertPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔔 ติดตาม/ตั้งค่าส่วนตัว", style=discord.ButtonStyle.green, custom_id="panel_sub")
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = load_db()
        u_id = str(interaction.user.id)
        if u_id not in db["subs"]:
            db["subs"][u_id] = {"thai": True, "global": False}
            save_db(db)
        await interaction.response.send_message("⚙️ ตั้งค่าการรับแจ้งเตือนใน DM ของคุณ:", view=SettingsView(u_id), ephemeral=True)

# --- [ 4. ตัวบอทหลัก ] ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.last_titles = set()

    async def setup_hook(self):
        self.add_view(AlertPanel())
        self.check_disaster.start()
        await self.tree.sync()

    @tasks.loop(minutes=1)
    async def check_disaster(self):
        sources = {
            "กรมอุตุฯ (ไทย)": "https://tmd.go.th/rss/warning.php",
            "แผ่นดินไหวทั่วโลก": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.atom"
        }
        async with aiohttp.ClientSession() as session:
            for name, url in sources.items():
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            feed = feedparser.parse(await resp.text())
                            for entry in feed.entries[:3]:
                                if entry.title not in self.last_titles:
                                    self.last_titles.add(entry.title)
                                    await self.broadcast_alert(entry, name)
                except: continue

    async def broadcast_alert(self, entry, src_name):
        db = load_db()
        is_global = "USGS" in src_name
        embed = discord.Embed(title=f"🚨 {src_name}", description=f"**{entry.title}**", color=0xff0000, timestamp=discord.utils.utcnow())
        
        # 1. ส่งลง Channel ในเซิร์ฟเวอร์
        ch_id = db["channels"].get("global" if is_global else "thai")
        if ch_id:
            channel = self.get_channel(int(ch_id))
            if channel: await channel.send(embed=embed)

        # 2. ส่งเข้า DM คนที่ติดตาม
        for u_id, setting in db["subs"].items():
            if (is_global and setting.get("global")) or (not is_global and setting.get("thai")):
                try:
                    user = await self.fetch_user(int(u_id))
                    await user.send(embed=embed)
                except: continue

bot = MyBot()

# --- [ 5. ระบบความปลอดภัย (Event Handling) ] ---
@bot.event
async def on_message(message):
    if message.author.bot: return
    db = load_db()

    async def violation(reason, content):
        await message.delete()
        await message.channel.send(f"❌ {message.author.mention} ทำผิดกฎ: **{reason}**", delete_after=5)
        try:
            em = discord.Embed(title="⚠️ คำเตือน", description=f"เหตุผล: {reason}\nข้อมูล: `{content}`", color=0xff0000)
            await message.author.send(embed=em)
        except: pass

    if db["protection"]["anti_link"] and re.search(r"http", message.content):
        return await violation("ห้ามส่งลิงก์", message.content)

    if db["bad_words"]["enabled"]:
        for word in db["bad_words"]["list"]:
            if word in message.content:
                return await violation("ใช้คำไม่สุภาพ", word)

    await bot.process_commands(message)

# --- [ 6. Slash Commands ] ---
@bot.tree.command(name="setup_all", description="ตั้งค่าทุกอย่างในครั้งเดียว")
async def setup_all(interaction: discord.Interaction, thai_ch: discord.TextChannel, global_ch: discord.TextChannel, welcome_ch: discord.TextChannel, leave_ch: discord.TextChannel, image_url: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ เฉพาะแอดมิน", ephemeral=True)
    
    db = load_db()
    db["channels"].update({"thai": str(thai_ch.id), "global": str(global_ch.id), "welcome": str(welcome_ch.id), "leave": str(leave_ch.id)})
    save_db(db)

    embed = discord.Embed(title="🛰️ ศูนย์ควบคุมภัยพิบัติ & ป้องกันเซิร์ฟเวอร์", description="เลือกกดปุ่มด้านล่างเพื่อรับแจ้งเตือนใน DM ส่วนตัว", color=0x2b2d31)
    if image_url.startswith("http"): embed.set_image(url=image_url)
    
    await interaction.channel.send(embed=embed, view=AlertPanel())
    await interaction.response.send_message("✅ ติดตั้งระบบทั้งหมดเรียบร้อย!", ephemeral=True)

# (รวมคำสั่ง badword_add, badword_setting, setup_protection จากโค้ดก่อนหน้าได้เลย)

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    TOKEN = os.getenv('BOT_TOKEN')
    if TOKEN: bot.run(TOKEN)
        
