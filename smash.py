#!/usr/bin/env python
import random


def bivariate(shift=1, sigma=1):
    rr = -1
    while rr < 0:
        rr = random.normalvariate(shift, sigma)
    return int(rr)


def smash(col_shift=1.5, row_shift=0.5, mean_shift=1):
    qwerty = ["qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
    row = random.choices(range(3), [0.1, 0.9, 0.1])[0]
    col = int(random.triangular(0, len(qwerty[row])))
    seq_len = int(random.triangular(8, 20))
    result = qwerty[row][col]
    col_rerolls = 0
    row_rerolls = 0
    direct = random.choice([1, -1])
    for _ in range(seq_len):
        rowinc = -100
        while not (0 <= row + rowinc < len(qwerty)):
            rowinc = int(random.normalvariate(0, row_shift))
            row_rerolls += 1
            row = row + rowinc
            colinc = -100
        while not (0 <= col + colinc < len(qwerty[row])):
            colinc = direct * bivariate(mean_shift, col_shift * (1 - 0.5 * (rowinc != 0)))
            col_rerolls += 1
            if random.random() <= 0.2 * col_rerolls:
                direct *= -1
            col = col + colinc
        if row < 0:
            row = 0
        if row >= len(qwerty):
            row = len(qwerty) - 1
        if col < 0:
            col = 0
        if col >= len(qwerty[row]):
            col = len(qwerty[row]) - 1
        result += qwerty[row][col]
    print(col_rerolls / seq_len, row_rerolls / seq_len)
    return result
