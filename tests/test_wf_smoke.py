import pytest
from harness import ALICE, BOB, GROUP, Harness


@pytest.fixture
async def h(tmp_path, clock):
    harness = await Harness.create(tmp_path, clock)
    yield harness
    await harness.close()


async def test_smoke_sprint(h):
    await h.say(ALICE, "/sprint 20 0")
    main = h.sprint_message()
    assert "Спринт розпочався" in main.text
    await h.press(BOB, main, "Долучитися")
    await h.advance(20 * 60)
    board = h.board()
    assert "Спринт завершено" in board.text, h.snapshot()
    await h.reply(ALICE, board, "500")
    print(h.snapshot(GROUP, ALICE))
    assert "500" in h.board().text
