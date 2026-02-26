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

# --- [ 1. ระบบ Flask สำหรับเปิด Port กันบอทหลับ ] ---
app = Flask('')
@app.route('/')
def home(): return "ระบบบอทโรงเรียนวังน้อย Online!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- [ 2. การตั้งค่าและฐานข้อมูล ] ---
TOKEN = os.getenv('BOT_TOKEN')
DB_FILE = 'config.json'

SOURCES = {
    "กรมอุตุฯ (ไทย)": "https://tmd.go.th/rss/warning.php",
    "แผ่นดินไหว (ไทย)": "https://tmd.go.th/rss/earthquake.php",
    "ปภ. (ภัยพิบัติไทย)": "https://www.disaster.go.th/th/rss/news_disaster.xml",
    "แผ่นดินไหวทั่วโลก (USGS)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.atom"
}

def load_config():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return {"subs": {}, "channels": {}}
    return {"subs": {}, "channels": {}}

def save_config(config):
    with open(DB_FILE, 'w') as f: json.dump(config, f)

# --- [ 3. ระบบ UI ปุ่มกดตั้งค่า (Settings) ] ---
class SettingsView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = str(user_id)
        config = load_config()
        self.settings = config["subs"].get(self.user_id, {"thai": True, "global": False})
        self.update_buttons()

    def update_buttons(self):
        self.thai_btn.style = discord.ButtonStyle.green if self.settings["thai"] else discord.ButtonStyle.grey
        self.thai_btn.label = "🇹🇭 ไทย: " + ("เปิด" if self.settings["thai"] else "ปิด")
        self.quake_btn.style = discord.ButtonStyle.green if self.settings["global"] else discord.ButtonStyle.grey
        self.quake_btn.label = "🌍 ทั่วโลก: " + ("เปิด" if self.settings["global"] else "ปิด")

    @discord.ui.button(label="🇹🇭 ไทย", custom_id="t_thai")
    async def thai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        self.settings["thai"] = not self.settings["thai"]
        config["subs"][self.user_id] = self.settings
        save_config(config)
        self.update_buttons()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🌍 ทั่วโลก", custom_id="t_global")
    async def quake_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        self.settings["global"] = not self.settings["global"]
        config["subs"][self.user_id] = self.settings
        save_config(config)
        self.update_buttons()
        await interaction.response.edit_message(view=self)

class AlertPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔔 ติดตาม/ตั้งค่าส่วนตัว", style=discord.ButtonStyle.green, custom_id="main_sub")
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        u_id = str(interaction.user.id)
        if u_id not in config["subs"]:
            config["subs"][u_id] = {"thai": True, "global": False}
            save_config(config)
        await interaction.response.send_message("⚙️ ตั้งค่าการรับแจ้งเตือนใน DM ของคุณ:", view=SettingsView(u_id), ephemeral=True)

    @discord.ui.button(label="🔕 ยกเลิกติดตาม", style=discord.ButtonStyle.danger, custom_id="main_unsub")
    async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        if str(interaction.user.id) in config["subs"]:
            del config["subs"][str(interaction.user.id)]
            save_config(config)
            await interaction.response.send_message("🔕 ยกเลิกการติดตามเรียบร้อย", ephemeral=True)
        else:
            await interaction.response.send_message("❌ คุณไม่ได้ติดตามอยู่แล้วครับ", ephemeral=True)

# --- [ 4. ตัวบอทหลักและการทำงาน ] ---
class DisasterBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.last_titles = set()

    async def setup_hook(self):
        self.add_view(AlertPanel()) # ทำให้ปุ่มใช้งานได้ถาวรแม้บอทรีสตาร์ท
        self.check_disaster.start()
        await self.tree.sync()

    @tasks.loop(minutes=1)
    async def check_disaster(self):
        async with aiohttp.ClientSession() as session:
            for src_name, url in SOURCES.items():
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            feed = feedparser.parse(await resp.text())
                            for entry in feed.entries[:3]:
                                if entry.title not in self.last_titles:
                                    self.last_titles.add(entry.title)
                                    await self.broadcast_alert(entry, src_name)
                except: continue

    async def broadcast_alert(self, entry, src_name):
        config = load_config()
        is_global = "USGS" in src_name
        
        embed = discord.Embed(title=f"🚨 {src_name}", description=f"**{entry.title}**", color=0xff0000, timestamp=discord.utils.utcnow())
        embed.set_footer(text="ระบบแจ้งเตือนภัย โรงเรียนวังน้อย")

        # ส่งลงช่องทางเซิร์ฟเวอร์
        target_ch_id = config["channels"].get("global" if is_global else "thai")
        if target_ch_id:
            channel = self.get_channel(int(target_ch_id))
            if channel: await channel.send(embed=embed)

        # ส่งเข้า DM รายบุคคล
        for u_id, setting in config["subs"].items():
            if (is_global and setting.get("global")) or (not is_global and setting.get("thai")):
                try:
                    user = await self.fetch_user(int(u_id))
                    await user.send(embed=embed)
                except: continue

bot = DisasterBot()

@bot.tree.command(name="setup_alert", description="ตั้งค่าห้องและส่งแผงควบคุม UI")
@app_commands.describe(thai_ch="ห้องแจ้งเตือนในไทย", global_ch="ห้องแจ้งเตือนทั่วโลก", image_url="ลิงก์รูปหน้าปกแผงควบคุม")
async def setup_alert(interaction: discord.Interaction, thai_ch: discord.TextChannel, global_ch: discord.TextChannel, image_url: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ สำหรับแอดมินเท่านั้น", ephemeral=True)
    
    config = load_config()
    config["channels"]["thai"] = str(thai_ch.id)
    config["channels"]["global"] = str(global_ch.id)
    save_config(config)

    embed = discord.Embed(title="🛰️ ระบบเฝ้าระวังภัยพิบัติ โรงเรียนวังน้อย", 
                          description=f"ตั้งค่าเรียบร้อย!\n🇹🇭 ข่าวไทย: {thai_ch.mention}\n🌍 ข่าวโลก: {global_ch.mention}\n\nกดปุ่มด้านล่างเพื่อเลือกรับแจ้งเตือนส่วนตัวใน DM", 
                          color=0x2b2d31)
    if image_url.startswith("http"): embed.set_image(url=image_url)
    
    await interaction.channel.send(embed=embed, view=AlertPanel())
    await interaction.response.send_message("✅ ติดตั้งแผงควบคุมและห้องส่งข้อมูลสำเร็จ!", ephemeral=True)

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    if TOKEN: bot.run(TOKEN)
        
