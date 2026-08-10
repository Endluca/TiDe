# 手机端布局视觉校验

- Source visual truth:
  - `/var/folders/f6/55s7pcrs7170gzgzt2f30j200000gn/T/codex-clipboard-4d2d7b42-8609-4dea-92e1-bf730da18252.png`
  - `/var/folders/f6/55s7pcrs7170gzgzt2f30j200000gn/T/codex-clipboard-000e68de-8a55-4627-9668-a7ae5908e752.png`
  - `/var/folders/f6/55s7pcrs7170gzgzt2f30j200000gn/T/codex-clipboard-2d38a983-6f43-41be-8215-57f905bba796.png`
  - `/var/folders/f6/55s7pcrs7170gzgzt2f30j200000gn/T/codex-clipboard-ea68c084-d8ef-47dd-8423-87c00f077869.png`
- Implementation screenshots:
  - `.codex-qa/mobile-toki-score-after.png`
  - `.codex-qa/mobile-personalized-after.png`
  - `.codex-qa/mobile-course-switch-after.png`
- Viewport: 390 × 844 CSS px, device scale factor 1.
- Source pixels: 357 × 292, 379 × 454, 363 × 159, 137 × 504.
- Implementation pixels: each 390 × 844; no density resampling.
- State: authenticated local preview, Chinese interface, mobile breakpoint.

## Full-view comparison evidence

The source and implementation images were opened together in one comparison pass. The final page keeps the existing visual system while reducing Toki-card dead space, separating four-digit scores from the unit, making multi-course navigation explicitly swipeable, and introducing a two-column personalized-task grid.

## Focused region comparison evidence

- Toki card: the illustration is 124 × 146 CSS px, moved upward beside the action content; the button now follows the action block instead of being pinned to the card bottom.
- Score rows: an 88 px score slot uses a 66 px tabular-number column, an 18 px unit column, and a 4 px gap. `1044 分` renders without overlap.
- Course switcher: the second card remains partially visible, the hint says “左右滑动切换课程”, scroll snapping works, and the mobile scrollbar is hidden.
- Personalized tasks: the current local account has one personalized task, which correctly spans the row. The two-item layout is covered by the responsive contract (`repeat(2, minmax(0, 1fr))`) and its regression test; a live two-record state was not available in this account.

## Findings

- Fonts and typography: existing families, weights and hierarchy are preserved; score digits use tabular numerals for stable alignment.
- Spacing and layout rhythm: the large gap below Toki content is removed; task cards and course tabs keep the existing mobile radii and spacing.
- Colors and visual tokens: unchanged; brand blue, yellow and state colors remain consistent.
- Image quality and asset fidelity: the existing Toki asset is reused at a larger size without replacement, clipping or visible blur.
- Copy and content: existing task copy is unchanged; only the mobile course-swipe hint was added.
- P0/P1/P2 findings: none after the second pass.
- P3 follow-up: visually recheck the two-personalized-task state when an account with two live assignments is available.

## Comparison history

1. First pass found the Toki illustration too small and the score unit colliding with four-digit values.
2. Toki was enlarged and raised; the score slot was widened and split into fixed number/unit columns.
3. Post-fix evidence shows the illustration adjacent to the content and `125 分` / `1044 分` separated and aligned.

## Verification

- Primary interactions tested: page navigation and horizontal course-tab scrolling.
- Responsive overflow: no page-level horizontal overflow at 390 px.
- Automated checks: 173 tests passed; production build passed; `git diff --check` passed.

final result: passed
