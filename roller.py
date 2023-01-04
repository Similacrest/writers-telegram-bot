import re
import random
from secrets import SystemRandom

class Roller:
    is_valid_command = False
    roll_regex = re.compile(rf'/(?:r(?:oll)?\s*)?(\d*)[dDкК](\d+)(mi\-?\d+)?(adv|dis)?([bm\+|\-]\d+)?(.*)')
    
    def __init__(self, txt):
        self._roll_details  = re.search(self.roll_regex, txt)
        self.is_valid_command = self._roll_details is not None
        self.best_random = SystemRandom()

    def execute_roll(self):
        numdice = dicemin = dicemax = 1
        numdice = int(self._roll_details[1]) if self._roll_details[1] else 1
        if not 0 < numdice <= 500:
            numdice = 1
        dicemax = int(self._roll_details[2])
        if not 0 < dicemax < 2 ** 31:
            dicemax = 20
        dicemin = int(self._roll_details[3][2:]) if self._roll_details[3] else 1
        if not -dicemax <= dicemin <= dicemax:
            dicemin = 1
        reroll_type = self._roll_details[4]
        bonus = int(self._roll_details[5].replace('b','').replace('m','-')) if self._roll_details[5] else 0
        caption = self._roll_details[6] if self._roll_details[6] else "Результат"

        prev_roll = None
        while True:
            pool = []
            for _ in range(numdice):
                pool.append(self.best_random.randint(dicemin, dicemax))
            roll = sum(pool)

            roll_vals = '['+', '.join([f"<b>{v}</b>" if v == dicemax else f"<u>{v}</u>" if v==dicemin else str(v) for v in pool]) + ']'
            if bonus != 0:
                roll_vals += ('+' if bonus > 0 else '') + str(bonus)
            roll_string = f"<code>{self._roll_details[1]}d{self._roll_details[2]}{self._roll_details[3] if self._roll_details[3] else ''}{self._roll_details[5] if self._roll_details[5] else ''} </code><b>{roll+bonus}</b>: {roll_vals}"
            if reroll_type and prev_roll is None:
                prev_roll = roll
                prev_roll_string = roll_string
                continue
            else:
                break
        result = f"<b>{caption} </b>"
        if not reroll_type:
            result += roll_string
        elif (reroll_type == 'adv' and prev_roll >= roll) or (reroll_type == 'dis' and prev_roll <= roll):
            result += f"\n<b>{prev_roll+bonus}</b>\n{prev_roll_string}\n<s>{roll_string}</s>"
        else:
            result += f"\n<b>{roll+bonus}</b>\n<s>{prev_roll_string}</s>\n{roll_string}"
        return result