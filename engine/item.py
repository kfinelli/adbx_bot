"""
Items and Equipment
><><><><><><><><><><><><><
"""
import warnings
from hmac import new

from engine.azure_constants import BUNDLE_SIZE, BundleData, ItemData, Slot, SortMode, Stat, ItemType


# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
# Items have a field called "prototype", which contains the UNMODIFIED item data as a dictionary.
# This means we can freely change the item stats and tags while still being able to reset it to normal, if things break.
# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><

class Item:
    def __init__(self, name, description = "", isLight = False):
        self.name = name
        self.description = description
        self.isLight = isLight
        self.updatePrototype()

    def setName(self, name):
        self.name = name
    def setDescription(self, description):
        self.description = description
    def setLightness(self, isLight):
        self.isLight = isLight

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
            ItemData.ITEM_TYPE: ItemType.ITEM,
            ItemData.NAME: self.name,
            ItemData.DESCRIPTION: self.description,
            ItemData.IS_LIGHT: self.isLight,
            ItemData.PROTOTYPE: self.prototype,
        }

    def updatePrototype(self, item=None):
        prototype = None
        if item is None:
            prototype = self.toDictionary()
        elif type(item) is not type(self):
            warnings.warn(
                f"\n'{item.name}' is a different item type than '{self.name}'"
                f"\n'{item.name}': '{type(item)}', '{self.name}': '{type(self)}'"
            )
            pass
        elif item is not None:
            prototype = item.toDictionary()
        else:
            prototype = self.toDictionary()
        #prototypes should probably not have a prototype, otherwise things could get weird.
        prototype.pop(ItemData.PROTOTYPE)
        self.prototype = prototype

# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><
# LightContainer is used for carrying light items.
# Contents should be empty in the container prototype, unless you intend for it to be a "refreshable" pack of some sort.
# NO LIGHT BUNDLES!!!!!! The system could honestly probably handle it, but let's just the headache.
# ><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><

class LightContainer(Item):
    defaultName = "Bundle"
    defaultDescription = "A collection of light items"

    def __init__(self, name=defaultName, description=defaultDescription, maxSize = BUNDLE_SIZE):
        super().__init__(name, description)
        self.maxSize = maxSize
        self.contents = list()
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
        contents = list()
        for item in self.contents:
            contents.append(item.toDictionary())
        exportData.update({
            ItemData.ITEM_TYPE: ItemType.LIGHT_CONTAINER,
            BundleData.MAX_SIZE: self.maxSize,
            BundleData.CONTENTS: contents,
        })
        return exportData

class EquipItem(Item):
    def __init__(self, name, rank, tags=None, otherAbilities=None, description="", isLight=False):
        super().__init__(name, description, isLight)
        if tags is None:
            tags = list ()
        self.rank = rank
        self.tags = tags
        self.otherAbilities = otherAbilities
        self.updatePrototype()

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
            ItemData.TAGS: self.tags,
            ItemData.OTHER_ABILITIES: self.otherAbilities,
            ItemData.RANK: self.rank,
            ItemData.PROTOTYPE: self.prototype,
        })
        return exportData

class Weapon(EquipItem):
    def __init__(self, name, rank, type, stat, damage, range=0, tags = None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.type = type
        self.stat = stat
        self.damage = max(0, damage)
        self.range = max(0, range)
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
            ItemData.TYPE: self.type,
            ItemData.STAT: self.stat,
            ItemData.DAMAGE: self.damage,
            ItemData.RANGE: self.range,
            ItemData.PROTOTYPE: self.prototype,
        })
        return exportData

class ChargeWeapon(Weapon):
    def __init__(self,name, rank, type, stat, damage, range=0, uses = 1, destroyOnEmpty=False, tags = None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, type, stat, damage, range, tags, otherAbilities, description, isLight)
        self.charges = uses
        self.maxCharges = uses
        self.destroyOnEmpty = destroyOnEmpty
        self.updatePrototype()

    def chageCharges(self,delta):
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
        exportData = super.toDictionary()
        exportData.update({
            ItemData.CHARGES: self.charges,
            ItemData.MAX_CHARGES: self.maxCharges,
            ItemData.DESTROY_ON_EMPTY: self.destroyOnEmpty,
            ItemData.PROTOTYPE: self.prototype,
        })
        return exportData

class Gear(EquipItem):
    def __init__(self, name, rank, slot, health, defense, resistance, tags=None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.slot = slot
        self.health = health
        self.defense = defense
        self.resistance = resistance
        self.updatePrototype()

    def toDictionary(self):
        exportData = super().toDictionary()
        exportData.update({
            ItemData.SLOT: self.slot,
            ItemData.HEALTH: self.health,
            ItemData.DEFENSE: self.defense,
            ItemData.RESISTANCE: self.resistance,
            ItemData.PROTOTYPE: self.prototype,
        })
        return exportData
