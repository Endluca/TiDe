# AI 客服悬浮图标视觉校验

- Source visual truth: `/var/folders/f6/55s7pcrs7170gzgzt2f30j200000gn/T/codex-clipboard-0a1931c1-d49a-40ac-8e68-710b31c6b93b.png`
- Implementation screenshot: `/tmp/ai-helper-centered-desktop.png`
- Combined comparison: `/tmp/ai-helper-before-after.png`
- Viewport: 1280 × 720 CSS px; desktop helper size 84 × 84 CSS px
- Pixels and density: source 117 × 126 px; implementation focused crop 125 × 145 px; device scale factor 1; no density resampling
- State: default, animation completed, AI Support label visible

## Full-view comparison evidence

The combined comparison shows the same supplied robot asset, circular button, yellow badge, and label. The robot face is moved slightly right while the component footprint and surrounding UI remain unchanged.

## Focused region comparison evidence

The component itself is the focused region, so no additional crop is required. The focused implementation screenshot confirms the face is visually centered without clipping the headset or placing the right eye under the badge.

## Findings

- Typography: unchanged; label family, weight, size, and line height remain consistent.
- Spacing/layout: the source PNG's asymmetric transparent padding made the artwork read left-heavy. `translate(8%, 2px)` corrects the visual center while preserving button geometry.
- Colors/tokens: unchanged.
- Image quality: the original PNG is reused without scaling or compression changes beyond the existing component sizing; no halo or crop regression is visible.
- Copy/content: unchanged.
- P0/P1/P2 findings: none.
- P3 follow-up: none required for the requested centering adjustment.

## Comparison history

No P0/P1/P2 issue was found, so no blocking QA iteration was required. The first implementation check showed the badge overlapping too much of the artwork after a stronger centering offset; the offset was reduced before the final comparison.

## Browser checks

- Desktop 84 px and compact 62 px states rendered.
- Browser console warnings/errors checked: none.
- Button remains present and accessible as `AI Support`.

final result: passed
