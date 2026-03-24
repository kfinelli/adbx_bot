"""
Items and Equipment
><><><><><><><><><><><><><
"""
import json
import warnings

from engine.azure_constants import (
    BUNDLE_SIZE,
    POWER_LEVEL,
    BundleData,
    ItemData,
    ItemType,
    RechargePeriod,
    SortMode,
    Stat,
)

# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
# Items have a field called "prototype", which contains the UNMODIFIED item data as a dictionary.
# This means we can freely change the item stats and tags while still being able to reset it to normal, if things break.
# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><

class Item:
    ITEM_TYPE = ItemType.ITEM.value
    def __init__(self, item_id, name, description = "", isLight = False):
        self.item_id = item_id
        self.name = name
        self.description = description
        self.isLight = isLight
        self.prototype = None
        if type(self) is Item:
            self.updatePrototype()

    def setName(self, name):
        self.name = name
    def setDescription(self, description):
        self.description = description
    def setLightness(self, isLight):
        self.isLight = isLight

    #Prototype Functions
    def resetToPrototype(self):
        resetItemToPrototype(self)

    def setPrototype(self, item=None):
        prototype = None
        # No item = update
        if item is None:
            self.updatePrototype()
            return
        # If dictionary and the item types match, set to the dictionary
        elif isinstance(item, dict) and self.prototype[ItemData.ITEM_TYPE] is item[ItemData.ITEM_TYPE]:
            prototype = item
        # Make sure items are the same item type, and leave if not
        elif type(item) is not type(self):
            warnings.warn(
                f"\n'{item.name}' is a different item type than '{self.name}'"
                f"\n'{item.name}': '{type(item)}', '{self.name}': '{type(self)}'",
                stacklevel=2
            )
            return
        #Export the item as a dictionary if we got to this point
        elif item is not None:
            prototype = item.toDictionary()

        # prototypes should probably not have a prototype. I am not sure we want that recursive weirdness.
        prototype.pop(ItemData.PROTOTYPE)
        self.prototype = prototype

    def updatePrototype(self):
        prototype = self.toDictionary()
        prototype.pop(ItemData.PROTOTYPE)
        self.prototype = prototype

    # ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
    # This is a function that LOVES to eat itself.
    # Each subclass calls to its superclass, until we end up back here.
    # The subclasses will then update this dictionary with their respective fields
    # ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
    def toDictionary(self):
        # ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
        # This is the base item dictionary.
        # All subclasses create prototypes by updating this dictionary, meaning fields
        # added here will appear in the prototypes of ALL items, including bundles.
        # ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
        return {
            ItemData.ITEM_ID.value: self.item_id,
            ItemData.NAME.value: self.name,
            ItemData.ITEM_TYPE.value: Item.ITEM_TYPE,
            ItemData.DESCRIPTION.value: self.description,
            ItemData.IS_LIGHT.value: self.isLight,
            ItemData.PROTOTYPE.value: self.prototype,
        }

    def toJSON(self):
        return json.dumps(self.toDictionary())

# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
# LightContainer is used for carrying light items.
# Contents should be empty in the container prototype, unless you intend for it to be a "refreshable" pack of some sort.
# NO LIGHT BUNDLES!!!!!! The system could honestly probably handle it, but let's just the headache.
# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><

class LightContainer(Item):
    defaultName = "Bundle"
    defaultDescription = "A collection of light items"
    ITEM_TYPE = ItemType.LIGHT_CONTAINER.value
    def __init__(self, item_id, name=defaultName, description=defaultDescription, maxSize = BUNDLE_SIZE):
        super().__init__(item_id, name, description)
        self.maxSize = maxSize
        self.contents = []
        if type(self) is LightContainer:
            self.updatePrototype()

    def addItem(self, item):
        if item.isLight and len(self.contents) < self.maxSize:
            self.contents.append(item)
        elif not item.isLight:
            raise ValueError(
                f"{item.name} is not a Light Item."
            )
        elif len(self.contents) >= self.maxSize:
            raise ValueError(
                f"{self.name} cannot hold any more items."
            )
    def removeItem(self, item):
        self.contents.remove(item)
    def removeItemByIndex(self, index):
        return self.contents.pop(index)
    def isFull(self):
        return len(self.contents) >= self.maxSize
    def sortContents(self, sortMode):
        if sortMode is SortMode.ALPHABETICAL:
            self.contents.sort(key=lambda x: x.name, reverse=True)
    def toDictionary(self):
        exportData = super().toDictionary()
        contents = []
        for item in self.contents:
            contents.append(item.toDictionary())
        exportData.update({
            ItemData.ITEM_TYPE.value: LightContainer.ITEM_TYPE,
            BundleData.MAX_SIZE.value: self.maxSize,
            BundleData.CONTENTS.value: contents,
        })
        return exportData

# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
# This is a helper class purely for managing more specific types of equipment.
# No items should ever be of type EquipItem, only subclasses thereof.
# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><

class EquipItem(Item):
    def __init__(self, item_id, name, rank, tags=None, otherAbilities=None, heldStatus=None, attackStatus=None, description="", isLight=False):
        super().__init__(item_id, name, description, isLight)
        if attackStatus is None:
            attackStatus = []
        if heldStatus is None:
            heldStatus = []
        if tags is None:
            tags = []
        self.rank = rank
        self.tags = tags
        self.otherAbilities = otherAbilities
        self.heldStatus = heldStatus
        self.attackStatus = attackStatus
        # We do not need to check for a prototype update here, because no item stops here.
        # if type(self) is type(EquipItem):
        #     self.updatePrototype()

    def setRank (self, rank):
        self.rank = rank
    def setOtherAbilities(self, otherAbilities):
        self.otherAbilities = otherAbilities

    def addTag(self, tag):
        self.tags.append(tag)
    def removeTag(self, tag):
        self.tags.remove(tag)
    def getTags(self):
        return self.tags

    def onEquip(self):
        pass
    def onUnequip(self):
        pass

    def toDictionary(self):
        exportData = super().toDictionary()
        exportData.update({
            ItemData.TAGS.value: self.tags,
            ItemData.OTHER_ABILITIES.value: self.otherAbilities,
            ItemData.HELD_STATUS.value: self.heldStatus,
            ItemData.ATTACK_STATUS.value: self.attackStatus,
            ItemData.RANK.value: self.rank,
            ItemData.PROTOTYPE.value: self.prototype,
        })
        return exportData



class Weapon(EquipItem):
    ITEM_TYPE = ItemType.WEAPON.value
    def __init__(self, item_id, name, rank, weaponType, stat, damage, range=0, tags = None, otherAbilities=None, heldStatus=None, attackStatus=None, description="", isLight = False):
        super().__init__(item_id, name, rank, tags, otherAbilities, heldStatus, attackStatus, description, isLight)
        self.type = weaponType
        self.stat = stat
        # Handle damage and range that may come as strings from JSON
        try:
            self.damage = max(0, int(damage) if damage not in (None, '', []) else 0)
        except (ValueError, TypeError):
            self.damage = 0
        try:
            self.range = max(0, int(range) if range not in (None, '', []) else 0)
        except (ValueError, TypeError):
            self.range = 0
        if type(self) is Weapon:
            self.updatePrototype()

    def setType(self, type):
        self.type = type
    def setStat(self, stat):
        if stat not in Stat:
            pass
        self.stat = stat
    def setRange(self, range):
        self.range = max(0, range)

    def setDamage(self, damage):
        self.damage = max(0, damage)
    def changeDamage(self, deltaDamage):
        self.damage = max(0, self.damage - deltaDamage)

    def toDictionary(self):
        exportData = super().toDictionary()
        exportData.update({
            ItemData.ITEM_TYPE.value: Weapon.ITEM_TYPE,
            ItemData.TYPE.value: self.type,
            ItemData.STAT.value: self.stat,
            ItemData.DAMAGE.value: self.damage,
            ItemData.RANGE.value: self.range,
            ItemData.PROTOTYPE.value: self.prototype,
        })
        return exportData

class ChargeWeapon(Weapon):
    ITEM_TYPE = ItemType.CHARGE_WEAPON.value
    def __init__(self, item_id, name, rank, weaponType, stat, damage, range=0, maxCharges = 1, destroyOnEmpty=False, tags = None, otherAbilities=None, heldStatus=None, attackStatus=None, description="", isLight = False):
        super().__init__(item_id, name, rank, weaponType, stat, damage, range, tags, otherAbilities, heldStatus, attackStatus, description, isLight)
        chargeData=parseChargeString(maxCharges)
        self.rechargePeriod = chargeData['rechargePeriod']
        self.charges = chargeData['maxCharges']
        self.maxCharges = chargeData['maxCharges']
        self.destroyOnEmpty = destroyOnEmpty
        if type(self) is ChargeWeapon:
            self.updatePrototype()

    def chargeCharges(self,delta):
        charges = max(0, self.charges + delta)
        self.charges = min(self.maxCharges, charges)
    def consumeCharge(self):
        self.charges -= 1
    def setCharges(self, charges):
        self.charges = charges
    def setMaxCharges(self, maxCharges):
        self.maxCharges = maxCharges
    def setDestroyOnEmpty(self, destroyOnEmpty):
        self.destroyOnEmpty = destroyOnEmpty

    def toDictionary(self):
        exportData = super().toDictionary()
        exportData.update({
            ItemData.ITEM_TYPE.value: ChargeWeapon.ITEM_TYPE,
            ItemData.CHARGES.value: self.charges,
            ItemData.MAX_CHARGES.value: self.maxCharges,
            ItemData.RECHARGE_PERIOD.value : self.rechargePeriod,
            ItemData.DESTROY_ON_EMPTY.value: self.destroyOnEmpty,
            ItemData.PROTOTYPE.value: self.prototype,
        })
        return exportData

class Gear(EquipItem):
    ITEM_TYPE = ItemType.GEAR.value
    def __init__(self, item_id, name, rank, slot, health, defense, resistance, tags=None, otherAbilities=None, heldStatus=None, attackStatus=None, description="", isLight = False,):
        super().__init__(item_id, name, rank, tags, otherAbilities, heldStatus, attackStatus, description, isLight)
        self.slot = slot
        self.health = health
        self.defense = defense
        self.resistance = resistance
        if type(self) is Gear:
            self.updatePrototype()

    def toDictionary(self):
        exportData = super().toDictionary()
        exportData.update({
            ItemData.ITEM_TYPE.value: ItemType.GEAR.value,
            ItemData.SLOT.value: self.slot,
            ItemData.HEALTH.value: self.health,
            ItemData.DEFENSE.value: self.defense,
            ItemData.RESISTANCE.value: self.resistance,
            ItemData.PROTOTYPE.value: self.prototype,
        })
        return exportData


def parseChargeString(charge_str):
    recharge = RechargePeriod.NEVER

    # Handle non-string inputs (e.g., integers from JSON)
    if not isinstance(charge_str, str):
        try:
            maxCharges = int(charge_str)
            recharge = RechargePeriod.NEVER
        except (ValueError, TypeError):
            recharge = RechargePeriod.INFINITE
            maxCharges = -1
        chargeData = {}
        chargeData['rechargePeriod'] = recharge
        chargeData['maxCharges'] = maxCharges
        return chargeData

    if '-' in charge_str or len(charge_str)==0:
        recharge = RechargePeriod.INFINITE
        maxCharges = -1
    elif '/' in charge_str:
        maxCharges = int(charge_str.split('/')[0])
    else:
        maxCharges = int(charge_str)

    if 'd' in charge_str:
        recharge = RechargePeriod.DAY
    elif 'e' in charge_str:
        recharge = RechargePeriod.ENCOUNTER

    chargeData = {}
    chargeData['rechargePeriod'] = recharge
    chargeData['maxCharges'] = maxCharges
    return chargeData

def resetItemToPrototype(item):
    item = createItemFromData(item.prototype)

# Leaving this in, just in case that we have a future use for it.
# Maybe if we make a DM item interface or something, so that we can still use in small numbers
def upscaleItemData(itemData):
    match itemData[ItemData.ITEM_TYPE]:
        case ItemType.GEAR:
            itemData[ItemData.HEALTH] *= POWER_LEVEL
            itemData[ItemData.DEFENSE] *= POWER_LEVEL
            itemData[ItemData.RESISTANCE] *= POWER_LEVEL
        case ItemType.CHARGE_WEAPON:
            itemData[ItemData.DAMAGE] *= POWER_LEVEL
        case ItemType.WEAPON:
            itemData[ItemData.DAMAGE] *= POWER_LEVEL
        case _:
            pass

def createItemFromData(itemData):
    if isinstance(itemData, str):
        itemData = json.loads(itemData)
        #upscaleItemData(itemData)
    elif isinstance(itemData, Item):
        itemData = itemData.toDictionary()
    newItem = None
    if ItemData.ITEM_TYPE not in itemData:
        #warnings.warn(f"No item type found in: {itemData}", stacklevel=2)
        return None
    match itemData[ItemData.ITEM_TYPE]:
        case ItemType.ITEM:
            newItem = Item(itemData[ItemData.ITEM_ID],
                           itemData[ItemData.NAME],
                           itemData[ItemData.DESCRIPTION],
                           itemData[ItemData.IS_LIGHT])
        case ItemType.WEAPON:
            newItem = Weapon(
                itemData[ItemData.ITEM_ID],
                itemData[ItemData.NAME],
                itemData[ItemData.RANK],
                itemData[ItemData.TYPE],
                itemData[ItemData.STAT],
                itemData[ItemData.DAMAGE],
                itemData[ItemData.RANGE],
                itemData[ItemData.TAGS],
                itemData[ItemData.OTHER_ABILITIES],
                itemData[ItemData.HELD_STATUS],
                itemData[ItemData.ATTACK_STATUS],
                itemData[ItemData.DESCRIPTION],
                itemData[ItemData.IS_LIGHT]
            )
        case ItemType.CHARGE_WEAPON:
            newItem = ChargeWeapon(
                itemData[ItemData.ITEM_ID],
                itemData[ItemData.NAME],
                itemData[ItemData.RANK],
                itemData[ItemData.TYPE],
                itemData[ItemData.STAT],
                itemData[ItemData.DAMAGE],
                itemData[ItemData.RANGE],
                itemData[ItemData.MAX_CHARGES],
                itemData[ItemData.DESTROY_ON_EMPTY],
                itemData[ItemData.TAGS],
                itemData[ItemData.OTHER_ABILITIES],
                itemData[ItemData.HELD_STATUS],
                itemData[ItemData.ATTACK_STATUS],
                itemData[ItemData.DESCRIPTION],
                itemData[ItemData.IS_LIGHT]
            )
            newItem.setCharges(itemData[ItemData.CHARGES])
        case ItemType.GEAR:
            newItem = Gear(
                itemData[ItemData.ITEM_ID],
                itemData[ItemData.NAME],
                itemData[ItemData.RANK],
                itemData[ItemData.SLOT],
                itemData[ItemData.HEALTH],
                itemData[ItemData.DEFENSE],
                itemData[ItemData.RESISTANCE],
                itemData[ItemData.TAGS],
                itemData[ItemData.OTHER_ABILITIES],
                itemData[ItemData.HELD_STATUS],
                itemData[ItemData.ATTACK_STATUS],
                itemData[ItemData.DESCRIPTION],
                itemData[ItemData.IS_LIGHT]
            )

        case ItemType.LIGHT_CONTAINER:
            newItem = LightContainer(
                itemData[ItemData.ITEM_ID],
                itemData[ItemData.NAME],
                itemData[ItemData.DESCRIPTION],
                itemData[BundleData.MAX_SIZE],
            )
            contentList = itemData[BundleData.CONTENTS]
            contents = []
            for i in contentList:
                contents.append(createItemFromData(i))
            newItem.contents = contents
        case _:
            warnings.warn(f"Unknown item type: {itemData[ItemData.ITEM_TYPE]}", stacklevel=2)
            return None
    return newItem
