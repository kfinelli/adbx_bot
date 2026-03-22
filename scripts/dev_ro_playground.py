import engine.azure_engine as az
import engine.item
import engine.azure_constants
from engine.azure_constants import Slot
from engine.item import Gear, ChargeWeapon, createItemFromData

testWeapon = ChargeWeapon(
    "Fire Tome", #name,
    "V", #rank,
    "Tome",#type,
    "RSN",#stat,
    6,#damage,
    1, #range,
    10,#maxCharges,
    False,#destroyOnEmpty,
    "[Black][Fire]", #tags,
    "Warm to the touch", #otherAbilities,
    "A small magic tome containing the power of fire within its pages", #description,
    True #isLight
)
testClone = createItemFromData(testWeapon)

testGear = Gear(
    "Casting Robes",
    "V",
    Slot.BODY.value,
    0.75,
    0.5,
    0.5,
    None,
    "Looks Magical",
    "A simple robe for novice casters",
    False
)
gearJson = testGear.toJSON()
gearClone = createItemFromData(gearJson)
print(gearClone.toJSON())

#Creating a character
"""
stats = az.rollStats()
azure = az.createCharacter("Azure", stats, 'knight')
azure.levelUp('knight')
azure.levelUp('mage')
print(azure)
"""


