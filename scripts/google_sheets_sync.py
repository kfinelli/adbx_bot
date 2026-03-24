import json
import os
from enum import StrEnum

import gspread

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — fall back to environment variables only
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY")

def getItemsFromSheet(sheet):
    sheetData = sheet.get_all_records()
    itemData = []
    for row in sheetData:
        if 'Name' in row and row["Name"] != "":
            itemData.append(row)
    return itemData

class ItemSheet(StrEnum):
    WEAPON = "Weapon"
    MAGIC = "Magic"
    HEAD = "Head"
    BODY = "Body"
    ARMS = "Arms"
    LEGS = "Legs"
    ACCESSORY = "Offhand/Accessory"
gc = gspread.api_key(GOOGLE_API_KEY)
sheets = gc.open_by_key(GOOGLE_SHEET_KEY)

itemData = {}
for sheet in ItemSheet:
    itemData[sheet.value] = getItemsFromSheet(sheets.worksheet(sheet))

print(json.dumps(itemData, indent=2, sort_keys=True))
#print(sheet.sheet1.get('A1'))

