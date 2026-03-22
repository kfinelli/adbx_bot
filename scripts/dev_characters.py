import engine.azure_engine as az

stats = az.rollStats()
azure = az.createCharacter("Azure", stats, 'knight')
azure.levelUp('knight')
azure.levelUp('mage')
print(azure)
