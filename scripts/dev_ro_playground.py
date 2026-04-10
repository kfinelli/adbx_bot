from engine.azure_constants import ItemSlot
from engine.item import ChargeWeapon, Gear, Weapon, createItemFromData

testWeapon = Weapon(
    "longsword",    # item_id
    "Longsword",    # name
    "C",            # rank
    "Sword",        # weaponType
    "PHY",          # stat
    8,              # damage
    0,              # range
    tags="[Fine]",
    otherAbilities="Warm to the touch",
    description="A well-balanced blade, warm to the touch",
)

testMagic = ChargeWeapon(
    "fire_tome",    # item_id
    "Fire Tome",    # name
    "V",            # rank
    "Tome",         # weaponType
    "RSN",          # stat
    6,              # damage
    1,              # range
    maxCharges=10,
    destroyOnEmpty=False,
    tags="[Black][Fire]",
    otherAbilities="Warm to the touch",
    description="A small magic tome containing the power of fire within its pages",
)
testClone = createItemFromData(testMagic.toJSON())

testGear = Gear(
    "casting_robes",    # item_id
    "Casting Robes",    # name
    "V",                # rank
    ItemSlot.BODY,      # slot
    0.75,               # health
    0.5,                # defense
    0.5,                # resistance
    otherAbilities="Looks Magical",
    description="A simple robe for novice casters",
)
gearJson = testGear.toJSON()
gearClone = createItemFromData(gearJson)
print(testWeapon.toJSON())
print(testClone.toJSON())
print(gearClone.toJSON())

# Character creation example using current APIs:
#
# from engine.azure_engine import CharacterClass
# from engine.character import CharacterManager
# from engine.dice import roll_stats
# from models import GameState, Party, Room
#
# state = GameState(party=Party(), room=Room())
# cm = CharacterManager()
# cm.create_character(state, "Azure", CharacterClass.KNIGHT,
#                     prerolled_stats=roll_stats())
# azure = state.party.members[0]
# print(azure.name, azure.level, azure.hp_max)
