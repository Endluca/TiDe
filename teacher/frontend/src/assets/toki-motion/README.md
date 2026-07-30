# Toki motion assets

These transparent, muted 512×512 clips are served from the configured public
asset base under `/assets/toki-motion/`. The production base is backed by OSS
and its CDN, while the existing static PNGs remain the poster and compatibility
fallback.

| Motion | Source | Duration | Runtime behavior |
| --- | --- | ---: | --- |
| `welcome` | 1 | 2.25 s | Play once |
| `celebrate` | 2 | 3.00 s | Play once |
| `encourage` | 3 | 3.00 s | Play once |
| `scan` | 4 | 2.50 s | Loop only while a review is pending |
| `unlock` | 5 | 3.00 s | Play once |
| `ticketReceived` | Jimeng ticket animation | 4.00 s | Play once after a support ticket is created |
| `allDone` | Jimeng stretch animation | 4.00 s | Play once when the growth tip is complete |

Each motion has a VP9 WebM primary asset and an HEVC-with-alpha MOV fallback.
The shared `Toki` component falls back to the existing static PNG when motion
is reduced or video playback is unavailable.
