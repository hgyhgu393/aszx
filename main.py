import discord
from discord.ext import tasks, commands
from discord import app_commands
import aiohttp
import feedparser
import json
import os
import re
from datetime import datetime

# --- [ ส่วนการตั้งค่า - แทนต้องแก้ตรงนี้ ] ---
TOKEN = 'MTQyNDcxMjIxMjgyMzU0Mzg1OQ.GKZtgq.8V0pIWNCdJCJ4hR2XCzh1nfMvhTIm_MDqNPoW4'
DB_FILE = 'subscribers.json'

# แหล่งข้อมูลภัยพิบัติไทย
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

# --- [ ระบบวิเคราะห์ข้อความและพิกัด ] ---
def parse_location(text):
    # ค้นหาละติจูดและลองจิจูด (ตัวเลขทศนิยม)
    coords = re.findall(r"(\d+\.\d+)", text)
    lat, lon = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
    
    # ดึงข้อมูล จังหวัด อำเภอ ตำบล
    area_match = re.search(r"((?:จังหวัด|จ\.)\s*\S+)|((?:อำเภอ|อ\.)\s*\S+)|((?:ตำบล|ต\.)\s*\S+)", text)
    location_summary = text if area_match else "ตรวจสอบรายละเอียดเพิ่มเติมในลิงก์ด้านล่าง"
        
    return lat, lon, location_summary

# --- [ UI ส่วนของปุ่มกด ] ---
class AlertView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # ปุ่มอยู่ถาวร

    @discord.ui.button(label="🔔 รับแจ้งเตือนด่วน (DM)", style=discord.ButtonStyle.green, custom_id="sub_v1")
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        subs = load_subs()
        if interaction.user.id not in subs:
            subs.append(interaction.user.id)
            save_subs(subs)
            await interaction.response.send_message("✅ บอทจะส่งแจ้งเตือนพร้อมแผนที่ให้ในแชทส่วนตัวของคุณทันที!", ephemeral=True)
        else:
            await interaction.response.send_message("📢 คุณเปิดรับการแจ้งเตือนไว้อยู่แล้วครับ", ephemeral=True)

    @discord.ui.button(label="🔕 ปิดแจ้งเตือน", style=discord.ButtonStyle.danger, custom_id="unsub_v1")
    async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        subs = load_subs()
        if interaction.user.id in subs:
            subs.remove(interaction.user.id)
            save_subs(subs)
            await interaction.response.send_message("🔕 ยกเลิกการแจ้งเตือนเรียบร้อยแล้ว", ephemeral=True)
        else:
            await interaction.response.send_message("❓ คุณไม่ได้เปิดการแจ้งเตือนไว้ตั้งแต่แรกครับ", ephemeral=True)

# --- [ ตัวบอทหลัก ] ---
class DisasterBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.last_titles = set()

    async def setup_hook(self):
        self.add_view(AlertView()) # ลงทะเบียนปุ่ม
        self.check_disaster.start() # เริ่มทำงานลูปเช็คข้อมูล
        await self.tree.sync() # ซิงค์คำสั่ง Slash Command

    @tasks.loop(minutes=3)
    async def check_disaster(self):
        async with aiohttp.ClientSession() as session:
            for source_name, url in SOURCES.items():
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            feed = feedparser.parse(await resp.text())
                            # ตรวจสอบ 3 รายการล่าสุด
                            for entry in feed.entries[:3]:
                                if entry.title not in self.last_titles:
                                    self.last_titles.add(entry.title)
                                    await self.broadcast_alert(entry, source_name)
                except Exception as e:
                    print(f"Error fetching {source_name}: {e}")

    async def broadcast_alert(self, entry, source_name):
        subs = load_subs()
        # วิเคราะห์พิกัดและพื้นที่
        lat, lon, area = parse_location(entry.title + " " + entry.description)
        
        embed = discord.Embed(
            title=f"🚨 {source_name}",
            description=f"**{entry.title}**",
            color=0xff0000,
            timestamp=discord.utils.utcnow()
        )
        
        # ใส่รายละเอียดพื้นที่
        embed.add_field(name="📍 พื้นที่ที่ได้รับผลกระทบ", value=f"```\n{area[:400]}\n```", inline=False)
        
        # ระบบแผนที่
        if lat and lon:
            # ลิงก์ไป Google Maps
            google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            embed.add_field(name="🗺️ การนำทาง", value=f"[คลิกเพื่อดูตำแหน่งบน Google Maps]({google_maps_url})", inline=False)
            
            # ใช้รูปแผนที่ Static (จุดสีแดง)
            static_map = f"https://www.mapquestapi.com/staticmap/v5/map?locations={lat},{lon}&size=600,400@2x&key=Fmjtd%7Cluurn16zn1%2C22%3Do5-9wt0gu&defaultMarker=marker-ff0000"
            embed.set_image(url=static_map)
        else:
            embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/179/179386.png")

        embed.add_field(name="🔗 ข้อมูลต้นทาง", value=f"[อ่านรายละเอียดจากเว็บไซต์]({entry.link})")
        embed.set_footer(text="ระบบแจ้งเตือนภัยพิบัติฉุกเฉิน (ประเทศไทย)")

        # ส่งหาทุกคนในฐานข้อมูล
        for user_id in subs:
            try:
                user = await self.fetch_user(user_id)
                await user.send(embed=embed)
            except:
                continue

# --- [ คำสั่งเริ่มต้นระบบ ] ---
bot = DisasterBot()

@bot.tree.command(name="setup_alert", description="สร้างแผงควบคุมระบบแจ้งเตือนภัยพิบัติ")
@app_commands.describe(message="ข้อความต้อนรับ", image_url="ลิงก์รูปหน้าปกระบบ")
async def setup_alert(interaction: discord.Interaction, message: str, image_url: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ คุณต้องเป็นแอดมินถึงจะใช้คำสั่งนี้ได้", ephemeral=True)
    
    embed = discord.Embed(
        title="🛰️ ระบบเฝ้าระวังภัยพิบัติแห่งชาติ",
        description=f"{message}\n\n**สถานะ:** 🟢 กำลังเฝ้าระวังตลอด 24 ชม.",
        color=0x2b2d31
    )
    embed.set_image(url=image_url)
    embed.set_footer(text="กดปุ่มด้านล่างเพื่อรับการแจ้งเตือนทันทีที่มีเหตุการณ์")
    
    await interaction.channel.send(embed=embed, view=AlertView())
    await interaction.response.send_message("✅ ติดตั้งระบบเรียบร้อยแล้ว!", ephemeral=True)

bot.run(TOKEN)
