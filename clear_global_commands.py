import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")


class ClearClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.clear_commands(guild=None)
        synced = await self.tree.sync(guild=None)
        print(f"グローバルコマンドをクリアしました: {len(synced)} 件")
        await self.close()


client = ClearClient()
client.run(TOKEN)