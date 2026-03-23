from enum import Enum,StrEnum
import gspread
import json
import os


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY")

def getItemsFromSheet(sheet):
    sheetData = sheet.get_all_records()
    itemData = list()
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

itemData = dict()
for sheet in ItemSheet:
    itemData[sheet.value] = getItemsFromSheet(sheets.worksheet(sheet))

print(json.dumps(itemData))
#print(sheet.sheet1.get('A1'))

