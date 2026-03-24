from engine.azure_constants import Slot
from engine.item import ChargeWeapon, Gear, Weapon, createItemFromData

testWeapon = Weapon(
    "Longsword", #name,
    "C", #rank,
    "Sword",#type,
    "PHY",#stat,
    8,#damage,
    0, #range,
    "[Fine]", #tags,
    "Warm to the touch", #otherAbilities,
    None,
    None,
    "A small magic tome containing the power of fire within its pages", #description,
    True #isLight
)

testMagic = ChargeWeapon(
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
    None,
    None,
    "A small magic tome containing the power of fire within its pages", #description,
    True #isLight
)
testClone = createItemFromData(testMagic.toJSON())

testGear = Gear(
    "Casting Robes",
    "V",
    Slot.BODY.value,
    0.75,
    0.5,
    0.5,
    None,
    "Looks Magical",
    None,
    None,
    "A simple robe for novice casters",
    False
)
gearJson = testGear.toJSON()
gearClone = createItemFromData(gearJson)
print(testWeapon.toJSON())
print(testClone.toJSON())
print(gearClone.toJSON())

#Creating a character
"""
stats = az.rollStats()
azure = az.createCharacter("Azure", stats, 'knight')
azure.levelUp('knight')
azure.levelUp('mage')
print(azure)
"""


