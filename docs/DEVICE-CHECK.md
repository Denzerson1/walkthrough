# Device check — 5 minutes on a real phone

What the emulated checks cannot tell: heat, real frame rate, real memory
pressure. Do this on one iPhone (Safari) and one Android phone (Chrome),
on the published site: <https://denzerson1.github.io/walkthrough/p/flat-01>.

Write down the phone, its OS version and the date. A ✗ anywhere is a bug.

## 1. Opening (1 min)

- [ ] The still of the living room shows within a second, with the title
      and "36 m²" (German on a German phone: "Rundgang starten").
- [ ] The button reads "Loading the flat…" with a bar, then "Start tour".
- [ ] "Start tour": the still fades into the 3D view at the same spot and
      the camera eases in for about 2.5 s. A tap during it ends it.
- [ ] With Reduce Motion on (iOS Settings › Accessibility › Motion), there
      is no camera move.

## 2. Smoothness (1 min)

- [ ] Drag to look round slowly, then quickly: no stutter, no white flashes.
- [ ] Tap the floor to walk to the bedroom, then back: smooth glides.
- [ ] Tap Bedroom, Hall, Living room in the room strip: each a smooth walk
      through the doorway, no jump in where you look.
- [ ] Open the design menu, apply Scandinavian, then Industrial: the
      pieces fade in within 2–3 s on mobile data, and the view keeps
      moving while they load.
- [ ] Kitchen tab: Sage, then Navy: the fronts and worktop change at once,
      lit as before. Wardrobe tab: Walnut, then White: the same.
- [ ] Drag the daylight slider from morning to evening: the furniture
      darkens with the room, lamps glow in the evening.

## 3. Heat and battery (2 min)

- [ ] Leave the phone on the tour, not touching it, for 2 minutes: the
      back of the phone stays cool (the view draws nothing while still).
- [ ] Then walk round for a minute: warm at most, not hot.

## 4. Switching away and back (30 s)

- [ ] Switch to another app for 20 s, come back: the tour is where it was,
      no reload, no black or white view.
- [ ] Lock the phone for 20 s, unlock: the same.

## 5. Stress (30 s)

- [ ] Switch styles ten times quickly, both rooms: no crash, no reload,
      no "A problem repeatedly occurred" page in Safari.
- [ ] Hold "Compare": the flat as it is; release: the style again.
- [ ] Reset: the flat as it is, the menu reads "As it is".

## Where to look if something fails

- Stutter: `apps/web/src/lib/quality.ts` lowers the pixel ratio after 1 s
  under 50 fps on average; on a phone it starts at 1.5x at most.
- Heat while still: the render loop stops after 2 s without input
  (`SplatScene.tsx`, `IDLE_AFTER_MS` + `SETTLE_MS`).
- Crash or reload: the furniture models are kept within 120 MB on a phone
  (`furnitureLayer.ts`, `BUDGET_BYTES`); a lost WebGL context is restored
  in place.
