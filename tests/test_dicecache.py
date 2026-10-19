"""The file ids of the dice stickers a bot has sent (core/dicecache.py)."""

import json

from writers_bot.core.dicecache import StickerCache


def test_put_is_kept_in_the_file_and_read_back_by_the_same_bot(tmp_path):
    path = tmp_path / "dice.json"
    cache = StickerCache(path)
    cache.load(7)
    cache.put("d20_17", "FILE1", "UNIQ1")
    cache.put("d6_3", "FILE2", "UNIQ2")
    assert json.loads(path.read_text())["bot_id"] == 7
    again = StickerCache(path)
    again.load(7)
    assert again.file_id("d20_17") == "FILE1" and again.file_id("d6_3") == "FILE2" and len(again) == 2
    assert again.is_roll_sticker("UNIQ1") and not again.is_roll_sticker("other")


def test_a_file_of_another_bot_keeps_only_the_unique_ids(tmp_path):
    path = tmp_path / "dice.json"
    first = StickerCache(path)
    first.load(7)
    first.put("d20_17", "FILE1", "UNIQ1")
    other = StickerCache(path)
    other.load(8)  # a file id belongs to one bot; the unique id is the same for every bot
    assert other.file_id("d20_17") is None and other.is_roll_sticker("UNIQ1")
    other.put("d20_17", "THEIRS", "UNIQ1")
    back = StickerCache(path)
    back.load(8)
    assert back.file_id("d20_17") == "THEIRS"


def test_forget_drops_the_file_id_but_the_sticker_is_still_known(tmp_path):
    cache = StickerCache(tmp_path / "dice.json")
    cache.load(1)
    cache.put("d20_17", "F", "U")
    cache.forget("d20_17")
    assert cache.file_id("d20_17") is None and cache.is_roll_sticker("U")
    again = StickerCache(tmp_path / "dice.json")
    again.load(1)
    assert again.file_id("d20_17") is None and again.is_roll_sticker("U")


def test_a_broken_file_is_ignored_and_a_missing_path_keeps_it_in_memory(tmp_path):
    path = tmp_path / "dice.json"
    path.write_text("{not json")
    cache = StickerCache(path)
    cache.load(1)
    assert len(cache) == 0
    cache.put("d6_3", "F", "U")  # and the file is made again
    assert json.loads(path.read_text())["stickers"]["d6_3"]["file_id"] == "F"
    memory = StickerCache(None)
    memory.load(1)
    memory.put("d6_3", "F", "U")
    assert memory.file_id("d6_3") == "F"
