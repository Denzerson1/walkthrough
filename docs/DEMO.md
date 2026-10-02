# Investor demo script

**Honest state:** this script runs against the *synthetic* test apartment.
There is no real capture yet, because no GPU was available to process one
(see `docs/PROGRESS.md`). The synthetic scene proves the product mechanics —
navigation, floor replacement, staging, sharing — but it does not look like a
real flat, and nobody should be allowed to think it does. Say so in the first
sentence of the demo.

Once a real capture exists, the script below works unchanged with a real
project id.

## Before they arrive

```bash
pnpm install
uv sync
pnpm seed:testscene
pnpm seed:floors
pnpm seed:assets
pnpm dev
```

Check:

- [ ] `http://localhost:8000/api/health` returns ok
- [ ] `http://localhost:5173/p/demo-01` loads and you can see the room
- [ ] The frame-rate counter reads 30+ on the machine you will demo from
- [ ] `ANTHROPIC_API_KEY` is set in `.env` if you intend to show the assistant
- [ ] Browser zoom at 100%, notifications off, one tab

If you are demoing the assistant, run `pnpm evals:assistant` beforehand and
know the number. Do not show it live without knowing the pass rate.

## The script — about 6 minutes

### 1. The problem (30 s, no screen)

Buyers book viewings to answer two questions: does the space work, and could
I live here. Photographs answer neither. Floor plans answer the first badly.

### 2. Stand in the room (60 s)

Open `/p/demo-01`.

- Drag to look around. Point out that the viewpoint does not move — you are
  standing where the camera stood, which is why it stays photoreal.
- Scroll or pinch to zoom. Mention it only changes field of view, never
  moves the camera. That is what stops it degrading into a bad 3D model.
- Click **Bedroom** in the room strip. The transition is a smooth second,
  deliberately — teleporting between rooms loses people.
- Point at the mini floor plan: real wall layout, real door openings, and
  where you are standing with your field of view drawn.

**The line:** "This is one capture from one iPhone walk-through. No special
rig."

### 3. Change the floor (90 s)

- **Floors** → pick a dark wood. It applies to the room you are standing in.
- Walk to the other room, show it is unchanged, come back.
- Tick **Apply to every room**, pick something pale. Whole flat changes.

**The line:** "Agents sell the flat they have. This sells the flat the buyer
imagines."

### 4. The honesty mechanism (45 s) — do not skip this

- Point at the amber **Virtually staged** badge.
- Hold **Hold to compare** to show the original capture, release.
- Hit **Reset**.

**The line:** "Every modified view is marked, and the original is always one
tap away. Virtual staging has a trust problem and a regulatory one. We are
not going to be the company that gets caught on it."

This is the slide that separates us from a render farm. Spend the time.

### 5. The assistant (90 s, only with an API key)

- **Assistant** → "make it feel Tuscan". It picks terracotta and explains why
  in one sentence.
- Then something absurd: "give me a floor made of lava". It declines and
  offers the nearest real option.

**The line:** "It can only choose from our catalog. It cannot invent a
product that does not exist, which is the failure mode that makes these
things unusable in sales."

If you have no API key, skip this and say the evals are written but
unmeasured. Do not fake it.

### 6. Share (30 s)

- **Share** → paste the link into a second window.
- The staged state comes back exactly.

**The line:** "An agent sends this to a buyer. The buyer sees the version
they designed. We see which rooms they lingered in and which floors they
tried."

### 7. Close (30 s)

Three things to land:

1. Capture cost is one person with a phone for twenty minutes.
2. The honesty mechanism is built in, not bolted on.
3. Everything in the catalog is licensed for commercial use and recorded in
   `docs/LICENSES.md`. No cleanup debt before launch.

## Questions you should expect

**"How long does processing take?"**
Unmeasured. Estimated 45–120 minutes per flat on one GPU, mostly unattended.
Say it is an estimate.

**"Does it run on a phone?"**
Yes, and we test on an iPhone viewport in CI. It has not yet been tried on
real iOS hardware.

**"How big is the download?"**
Target is under 60 MB per apartment. The synthetic scene is 24 MB as PLY. SPZ
compression will cut that substantially once the encoder is installed.

**"What about the furniture?"**
Placement, collision and layout logic are real and tested. The meshes are
not sourced yet — that is a budget decision, costed in `docs/ROADMAP.md`.

**"Can you remove the furniture that's already there?"**
Out of scope deliberately, and the design note explaining why is in
`docs/ROADMAP.md`. Removing it is easy; convincingly inpainting the wall
behind it is not.

## Do not do these

- Do not demo on hotel wifi without the scene cached.
- Do not run the assistant live without knowing the eval pass rate.
- Do not call the synthetic scene a real apartment.
- Do not promise a processing time you have not measured.
