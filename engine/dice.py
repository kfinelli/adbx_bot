"""
Dice rolling utilities for the dungeon crawler engine.
"""

import random
import re


def print_dice_results(results):
  diceOutput = ""
  for die in results['dice']:
    diceOutput += str(die)+", "
  diceOutput= diceOutput.rstrip(", ")
  print("Dice: ", diceOutput)
  print("Bonus: ", results['bonus'])
  print("Total: ", results['total'])


#  Rolls a XdY+Z expression (strict order)
#  or returns a number if a number is given
#    Returns a dictionary with the following keys:
#    'bonus': bonus applied to the roll
#    'dice': list of all rolled dice
#    'total': total of all rolled dice
def roll_dice_expr(expr):
    xyz = re.split(r'd|\+', expr)
    x = int(xyz[0])
    if len(xyz) == 1:
        return {'dice':{x},'total':x, 'bonus':0}
    if "d" not in expr:
        z = int(xyz[1])
        return {'dice': {x}, 'total': x + z, 'bonus': z}
    y = int(xyz[1])
    if len(xyz) == 2 and y != 0:
        return roll_expr(x, y)
    z = 0
    if len(xyz) > 2:
        z = int(xyz[2])
    if y == 0:
        return {'dice': {x}, 'total': x+z, 'bonus': z}
    return roll_expr(x,y,z)


def roll_expr(dCount, dSize, bonus=0):
    result = {'dice':[],'bonus':bonus,'total':bonus}
    list = [0] * dCount
    for i in range(int(dCount)):
        list[i] = d(dSize)
        result['total'] += list[i]
    result['dice'] = list
    return result

def d(x):
    """Roll a single die with x sides.  Returns 0 if x < 1."""
    x = int(x)
    if x < 1:
        return 0
    return random.randint(1, x)


def roll(n: int, sides: int) -> list[int]:
    """Roll n dice of `sides` sides, return individual results."""
    return [random.randint(1, sides) for _ in range(n)]


def roll_sum(n: int, sides: int) -> int:
    """Roll n dice of `sides` sides and return the sum."""
    return sum(roll(n, sides))


def roll_azure_stat() -> int:
    """
    Roll one Azure stat using the formula 20d40 − 400
    Range: −500 to +300 (i.e. −5.00 to +3.00).
    """
    return roll_sum(20,40) - 400


def roll_stat_block():
    """Roll all four Azure stats and return an AzureStats instance."""
    from models import AzureStats
    return AzureStats(
        physique=roll_azure_stat(),
        finesse=roll_azure_stat(),
        reason=roll_azure_stat(),
        savvy=roll_azure_stat(),
    )


def roll_stats() -> dict:
    """
    Roll stats for a new character and return as a plain dict.
    Keys are the four Azure stat names; values are POWER_LEVEL-scaled integers.
    Used by the /arrive DM conversation before character creation.
    """
    block = roll_stat_block()
    return {
        "physique": block.physique,
        "finesse":  block.finesse,
        "reason":   block.reason,
        "savvy":    block.savvy,
    }
