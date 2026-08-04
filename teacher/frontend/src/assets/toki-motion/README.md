# Toki motion assets

These transparent, muted 512×512 clips are served from the configured public
asset base under `/assets/toki-motion/`. The production base is backed by OSS
and its CDN. Animated Toki videos intentionally omit a static poster so the PNG
does not flash before playback. After a one-shot motion finishes, the shared
Toki component fades into the matching static PNG. Static PNGs also remain the
default for non-animated and reduced-motion states.

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
The shared `Toki` component uses the existing static PNG when no motion is
configured or when the user prefers reduced motion.
