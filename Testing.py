import AzureTables as az

stats = az.rollStats()
azure = az.createCharacter("Azure", stats, 'knight')
azure.levelUp('knight')
azure.levelUp('mage')
print(azure)