"""
Items and Equipment
><><><><><><><><><><><><><
"""
from engine.azure_constants import Stat, Slot, SortMode



class Item:
    def __init__(self, name, description = "", isLight = False):
        self.name = name
        self.description = description
        self.isLight = isLight

    def setName(self, name):
        self.name = name
    def setDescription(self, description):
        self.description = description
    def setLightness(self, isLight):
        self.isLight = isLight

class LightContainer(Item):
    defaultName = "Bundle"
    defaultDescription = "A collection of light items"
    defaultMaxSize = 10

    def __init__(self, name=defaultName, description=defaultDescription, maxSize = defaultMaxSize):
        super().__init__(name, description)
        self.maxSize = maxSize
        self.contents = list()

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
        
class EquipItem(Item):
    def __init__(self, name, rank, tags=None, otherAbilities=None, description="", isLight=False):
        super().__init__(name, description, isLight)
        if tags is None:
            tags = list ()
        self.rank = rank
        self.tags = tags
        self.otherAbilities = otherAbilities

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

class Weapon(EquipItem):
    def __init__(self, name, rank, type, stat, damage, range=0, tags = None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.type = type
        self.stat = stat
        self.damage = max(0, damage)
        self.range = max(0, range)

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

class Gear(EquipItem):
    def __init__(self, name, slot, rank, health, defense, resistance, tags=None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.slot = slot
        self.health = health
        self.defense = defense
        self.resistance = resistance