# Cinematic media drop-ins

The landing page (`/marketing`) ships with a self-contained canvas hero (particles that
assemble the case dashboard as you scroll). It **automatically upgrades** to a generated
video when one exists here — no code change needed:

| File | Used as |
|---|---|
| `rg-hero.mp4` | Hero background, scroll-scrubbed (scrolling drives `video.currentTime`) |

Guidelines for the render (Veo / Seedance via ElevenLabs or any generator):

- 16:9, 1080p, ~8–10 s, **no audio** (the scrubber mutes anyway).
- Design the motion to read well when *scrubbed*, not played: one continuous camera move,
  no hard cuts.
- Compress for web before dropping in (H.264, ~4–6 Mbps is plenty):
  `ffmpeg -i in.mp4 -an -c:v libx264 -crf 26 -movflags +faststart rg-hero.mp4`

Files here are served by `GET /media/{name}` (mp4/webm/jpg/png/webp only).
