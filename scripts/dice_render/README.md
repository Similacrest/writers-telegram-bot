# Dice sticker renderer

Makes the two sticker packs of the new dice (`src/writers_bot/assets/dice_packs`): the "Dice of Rolling" theme
drawn frame by frame with [Babylon.js](https://www.babylonjs.com/) in headless Chromium, then encoded as WEBM
(VP9, 512 px, 2.9 s, at most 256 KB: Telegram's limits for video stickers).

Nothing is recorded. Each die is posed so the face with the wanted number looks at the camera, so any result can be
made exactly (a physics roll would have to be repeated until the number came up). The roll is a spin that slows to a
stop with a small hop, 0.8 s, then the die lies still; the effects around it run through the whole loop.

## Files

| File | What it does |
|---|---|
| `fetch_assets.py` | downloads Babylon.js, the theme and the coin scan (pinned SHA-256) into `build/dice_render/web`, then runs `prep_atlas` |
| `prep_atlas.py` | splits the theme's texture into the marble and crisp white numerals (the original's 25 px numerals turn chalky) |
| `render.py` | draws and encodes the clips into `build/dice_render/out`; the clip names and the packs are defined here |
| `tags.py` | the emoji and search keywords of every sticker (the vocabulary lives here: edit it, run `packs.py`) |
| `packs.py` | checks every clip against the limits, then writes the packs and `manifest.json` (in the packs' order) into the bot's assets |
| `calibrate.py` | finds the turn that puts each face's numeral upright (`roll_offsets.json`); only needed for other dice or a new texture |
| `page/render.html` | the Babylon.js page: the scene, the poses, the tumble, the texture layers |

## Setting up

```
uv run --group render python scripts/dice_render/fetch_assets.py
uv run --group render playwright install chromium-headless-shell
```

Chromium needs some system libraries (on Debian: `libnss3 libnspr4 libatk1.0-0t64 libxcomposite1 libxdamage1
libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libasound2t64 libatspi2.0-0t64 libdrm2 libxi6`). With root,
`playwright install-deps` does it. Without, `apt-get download` them, unpack with `dpkg-deb -x` into a folder and point
`LD_LIBRARY_PATH` at its `usr/lib/<arch>-linux-gnu`. No GPU is needed (software WebGL), and a clip takes about a
minute. Heavy on a small machine: keep `TMPDIR` on disk, and cap the memory (`systemd-run --user --scope -p
MemoryMax=4G ...`).

## Making the packs

```
uv run --group render python scripts/dice_render/render.py --pack 1 --pack 2     # 240 clips, 3 to 4 hours
uv run --group render python scripts/dice_render/render.py --pack 2 --shard 1/2  # or in halves, side by side
uv run --group render python scripts/dice_render/render.py d20_17 d12rd_5        # a few
uv run --group render python scripts/dice_render/packs.py                         # check, then write the assets
```

Existing clips are skipped (`--force` redoes them). `packs.py` writes nothing unless every clip passes. The same
frames always come out (the files differ by a few bytes of muxer metadata), so a changed look shows in a diff of
the decoded frames, not the files.

## What is in the packs

Clip names (`render.py` has the lists): `d20_17` a d20; `d20a_17` / `d20d_17` the higher / lower of two d20s; `d12r_9` /
`d12b_9` a red / blue d12, `d12rd_9` red for the lower of two, `d12ba_9` blue for the higher; `d4n_1`, `d6n_6`,
`d10n_10` the lowest and highest without the crit effects; `d100_73` a tens die and a d10 (landing a moment apart);
`d2_1` / `d2_2` the coin, the coat of arms / "5 копійок".

The order in a pack is the order of the stickers in it: by die, values ascending, the crit 1 before the crit-less 1 and
the crit-less highest before the crit highest.

- **Pack 1 (120):** d20 1-20 with its crits; d20a and d20d 2-19 (a tied 1 or 20 is the plain d20); d12r, d12b (no crit
  effects: for Xd12, where crits work differently), d12rd, d12ba (with them: for a lone d12) 1-12 each; d10 1-10; and
  d10n 1/10, d4n 1/4, d6n 1/6.
- **Pack 2 (120):** d100 1-100, d4, d6, d8 (with crits) and the coin.
- Effects (`manifest.json`, `effect`): the highest roll of a die gets golden rays, sparkles and a gold halo (`crit`),
  the lowest a red halo, a dull die, a shudder and a puff of dust (`fumble`), the higher / lower of two d20s a quiet green /
  magenta halo (`adv`, `dis`).

## Notes on the look

- The theme's bump map is not used: its low-resolution normals draw dotted outlines on the numerals.
- The rolled face keeps the theme's colours and full white numerals; the other numerals are faded (`FADE` in the page).
- The d12 and the d100's tens die come as light marble with dark numerals in the theme; they are coloured and
  their numerals made white (the d12 red or blue, the tens die pink); a d20 is turned orange, so no two dice types share a colour.
- A d4 is read at its top vertex, so it is drawn looking at that vertex, the number three times, turned a little off the
  camera axis (`tilt4` in the page) so one of the three faces shows more.
- The d100's two dice sit close together (`x=±0.43` in `render.py`).
- Numerals that came out wrong in the automatic calibration were set by hand in `roll_offsets.json`: the d12's 10
  and 11, the d100's 10 and 20. The d10's 0 and the tens die's 00 are symmetric, so the calibration could not tell their
  turn: they were drawn upside down (the numeral in the top of the face, the point down) until v4.2.6 turned them by 180°.
  Look at the contact sheet of a die's stills for such a face.

## Licences of what it fetches

- Babylon.js 5.57.1: Apache-2.0.
- Dice of Rolling theme and models, by Frank Ali (github.com/3d-dice/dice-themes): the repository is MIT, and its
  README calls the models and themes CC0.
- The 5 kopiyok scan: Wikimedia Commons, `5-kopiyok-Ukraine.jpg`, tagged `PD-UA-exempt` (coins are not subject to
  copyright in Ukraine).
