# Dice sticker renderer

Makes the dice sticker clips (`src/writers_bot/assets/dice_clips`; the bot sends them one by one, no sticker packs are made): the "Dice of Rolling" theme
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
| `clips.py` | what the clips are: the name grammar, each clip's dice, looks and effect |
| `render.py` | draws and encodes the clips into `build/dice_render/out` (the dice's positions and colours are here) |
| `assets.py` | checks every clip against the limits for a video sticker, then writes them and `manifest.json` into the bot's assets |
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
`LD_LIBRARY_PATH` at its `usr/lib/<arch>-linux-gnu`. No GPU is needed (software WebGL), and a clip takes 10 to 20 seconds
on one core. Heavy on a small machine: keep `TMPDIR` on disk, and cap the memory (`systemd-run --user --scope -p
MemoryMax=1800M ...` for each shard: a shard peaks at about 1.4 GB with its Chromium, so three fit in 6 GB).

## Making the clips

```
uv run --group render python scripts/dice_render/render.py --all                         # all 866 clips, about an hour with 3 shards
uv run --group render python scripts/dice_render/render.py --all --shard 1/3             # a third of them; 2/3 and 3/3 beside it
uv run --group render python scripts/dice_render/render.py d20_17 3d6_1_4_4 2d20a_5_17   # a few
uv run --group render python scripts/dice_render/assets.py                               # check, then write the assets
```

Existing clips are skipped (`--force` redoes them), so delete the clips of a die whose look changed. `assets.py` writes nothing
unless every clip passes. The same frames always come out (the files differ by a few bytes of muxer metadata), so a changed
look shows in a diff of the decoded frames, not the files.

## What is in the clips

Clip names (the grammar is at the top of `clips.py`): `d20_17` a d20 with the crit effects on its ends, `d20n_20` without them
(a die rolled with others; also `d4n`, `d6n`, `d8n`, `d10n`, `d12n`, `d100n`); `d3_2` the d3; `d12_5` a blue d12 (its 1 is red);
`d100_73` a tens die and a d10 (landing a moment apart; also the pictures of `2d10`); `d2_1` / `d2_2` the coin; `3d6_1_4_4` two
or three dice of a kind side by side, unordered (`2d4`, `3d4`, `2d6`, `3d6`, `2d8`); `2d12_9_4` Daggerheart's duality dice, the
blue 9 and the red 4; `2d20a_5_17` / `2d20d_5_17` two d20s with advantage / disadvantage, `2d20t_9` two alike. 866 in all: 179
singles, 143 for the pools, 144 duality pairs and 400 for two d20s.

- Effects (`manifest.json`, `effect`): `crit` is golden rays, sparkles and a gold halo, `fumble` a red halo, a dull die, a
  shudder and a puff of dust. A group of dice has `crit` when any die shows its highest and else `fumble` when any shows a 1;
  with advantage the die that counts decides (the higher one, the lower one with disadvantage); the d12 pairs and the d3 and
  the coin have none. The die that counts also has a quiet halo, green for advantage and magenta for disadvantage; in a
  duality pair the larger die has its own (green on the blue die, magenta on the red one).

## Notes on the look

- The theme's bump map is not used: its low-resolution normals draw dotted outlines on the numerals.
- The rolled face keeps the theme's colours and full white numerals; the other numerals are faded (`FADE` in the page).
- The d12 and the d100's tens die come as light marble with dark numerals in the theme; they are coloured and
  their numerals made white (the d12 red or blue, the tens die pink); a d20 is turned orange, so no two dice types share a colour.
- The d3 is modelled in `page/render.html` (a ball cut by three flats, a numeral on each flat); every other die is the theme's.
- Each die type has a colour of its own: d3 grey, d4 purple, d6 yellow, d8 black, d10 green, d12 blue (red for its 1 and the
  duality's second die), d20 orange, the d100's tens die pink. The d6, d8 and d20 are the theme's marble turned (`HUES` in `render.py`).
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
