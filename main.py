import discord
from discord.ext import tasks, commands
from discord import app_commands
import aiohttp
import feedparser
import json
import os
import re
from datetime import datetime

# --- [ ส่วนการตั้งค่าระบบ ] ---
# โค้ดจะดึงค่าจากช่อง Key ที่ชื่อ BOT_TOKEN ในหน้า Environment ของ Render
TOKEN = os.getenv('BOT_TOKEN') 
DB_FILE = 'subscribers.json'

SOURCES = {
    "กรมอุตุนิยมวิทยา (เตือนภัยพายุ/ฝน)": "https://tmd.go.th/rss/warning.php",
    "ศูนย์เฝ้าระวังแผ่นดินไหว": "https://tmd.go.th/rss/earthquake.php",
    "ปภ. (ข่าวภัยพิบัติ/น้ำท่วม)": "https://www.disaster.go.th/th/rss/news_disaster.xml"
}

# --- [ ระบบฐานข้อมูล ] ---
def load_subs():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return []
    return []

def save_subs(subs):
    with open(DB_FILE, 'w') as f: json.dump(subs, f)

# --- [ ระบบวิเคราะห์ข้อความ ] ---
def parse_location(text):
    coords = re.findall(r"(\d+\.\d+)", text)
    lat, lon = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
    area_match = re.search(r"((?:จังหวัด|จ\.)\s*\S+)|((?:อำเภอ|อ\.)\s*\S+)|((?:ตำบล|ต\.)\s*\S+)", text)
    location_summary = text if area_match else "ตรวจสอบรายละเอียดเพิ่มเติมในลิงก์ด้านล่าง"
    return lat, lon, location_summary

# --- [ UI ปุ่มกด ] ---
class AlertView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔔 รับแจ้งเตือนด่วน (DM)", style=discord.ButtonStyle.green, custom_id="sub_v1")
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        subs = load_subs()
        if interaction.user.id not in subs:
            subs.append(interaction.user.id)
            save_subs(subs)
            await interaction.response.send_message("✅ บอทจะส่งแจ้งเตือนภัยพิบัติให้ใน DM นะครับ!", ephemeral=True)
        else:
            await interaction.response.send_message("📢 คุณเปิดรับแจ้งเตือนไว้อยู่แล้วครับ", ephemeral=True)

    @discord.ui.button(label="🔕 ปิดแจ้งเตือน", style=discord.ButtonStyle.danger, custom_id="unsub_v1")
    async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        subs = load_subs()
        if interaction.user.id in subs:
            subs.remove(interaction.user.id)
            save_subs(subs)
            await interaction.response.send_message("🔕 ยกเลิกเรียบร้อยครับ", ephemeral=True)

# --- [ ตัวบอทหลัก ] ---
class DisasterBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.last_titles = set()

    async def setup_hook(self):
        self.add_view(AlertView())
        self.check_disaster.start()
        await self.tree.sync()

    @tasks.loop(minutes=3)
    async def check_disaster(self):
        async with aiohttp.ClientSession() as session:
            for source_name, url in SOURCES.items():
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            feed = feedparser.parse(await resp.text())
                            for entry in feed.entries[:3]:
                                if entry.title not in self.last_titles:
                                    self.last_titles.add(entry.title)
                                    await self.broadcast_alert(entry, source_name)
                except Exception as e:
                    print(f"Error checking {source_name}: {e}")

    async def broadcast_alert(self, entry, source_name):
        subs = load_subs()
        lat, lon, area = parse_location(entry.title + " " + entry.description)
        embed = discord.Embed(title=f"🚨 {source_name}", description=f"**{entry.title}**", color=0xff0000, timestamp=discord.utils.utcnow())
        embed.add_field(name="📍 พื้นที่", value=f"```\n{area[:400]}\n```", inline=False)
        if lat and lon:
            embed.add_field(name="🗺️ แผนที่", value=f"[เปิด Google Maps](https://www.google.com/maps/search/?api=1&query={lat},{lon})", inline=False)
            static_map = f"https://www.mapquestapi.com/staticmap/v5/map?locations={lat},{lon}&size=600,400@2x&key=Fmjtd%7Cluurn16zn1%2C22%3Do5-9wt0gu&defaultMarker=marker-ff0000"
            embed.set_image(url=static_map)
        embed.set_footer(text="ระบบแจ้งเตือนภัยพิบัติ (โรงเรียนวังน้อย)")
        for user_id in subs:
            try:
                user = await self.fetch_user(user_id)
                await user.send(embed=embed)
            except: continue

bot = DisasterBot()

@bot.tree.command(name="setup_alert", description="ติดตั้งแผงควบคุมการแจ้งเตือน")
async def setup_alert(interaction: discord.Interaction, message: str, image_url: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นครับ", ephemeral=True)
    embed = discord.Embed(title="🛰️ ระบบเฝ้าระวังภัยพิบัติ", description=message, color=0x2b2d31)
    embed.set_image(url=image_url)
    await interaction.channel.send(embed=embed, view=AlertView())
    await interaction.response.send_message("✅ ติดตั้งเรียบร้อย!", ephemeral=True)

# --- [ ส่วนเริ่มต้นการทำงาน ] ---
if __name__ == "__main__":
    if TOKEN:
        print("✅ พบ Token แล้ว! กำลังเริ่มรันบอท...")
        bot.run(TOKEN)
    else:
        print("❌ ERROR: บอทหาค่า 'BOT_TOKEN' ไม่เจอ!")
        print("กรุณาเช็คในหน้า Environment ของ Render ว่าชื่อ Key ตรงกับคำว่า 'BOT_TOKEN' หรือไม่")
    
