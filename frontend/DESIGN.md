---
name: 발권창구 (Bal-Gwon-Chang-Gu)
colors:
  surface: "#0E1014"
  surface-dim: "#0B0D11"
  surface-bright: "#1A1D24"
  surface-container-lowest: "#08090C"
  surface-container-low: "#13161B"
  surface-container: "#161A20"
  surface-container-high: "#1B1F26"
  surface-container-highest: "#21252D"
  on-surface: "#ECE7DD"
  on-surface-variant: "#9C9588"
  outline: "#3E4148"
  outline-variant: "#272A30"
  surface-tint: "#ECE7DD"
  inverse-surface: "#ECE7DD"
  inverse-on-surface: "#0E1014"
  inverse-primary: "#0E1014"
  primary: "#ECE7DD"
  on-primary: "#0E1014"
  primary-container: "#21252D"
  on-primary-container: "#ECE7DD"
  primary-fixed: "#ECE7DD"
  primary-fixed-dim: "#C8C3B9"
  on-primary-fixed: "#0E1014"
  on-primary-fixed-variant: "#36393E"
  secondary: "#5BA8E5"
  on-secondary: "#001D33"
  secondary-container: "#003A66"
  on-secondary-container: "#CDE3F7"
  secondary-fixed: "#CDE3F7"
  secondary-fixed-dim: "#9DC8EA"
  on-secondary-fixed: "#001D33"
  on-secondary-fixed-variant: "#003A66"
  tertiary: "#E26B7A"
  on-tertiary: "#3F0006"
  tertiary-container: "#73121E"
  on-tertiary-container: "#FFD9DD"
  tertiary-fixed: "#FFD9DD"
  tertiary-fixed-dim: "#F0A8B0"
  on-tertiary-fixed: "#3F0006"
  on-tertiary-fixed-variant: "#73121E"
  error: "#E26B7A"
  on-error: "#3F0006"
  error-container: "#73121E"
  on-error-container: "#FFD9DD"
  background: "#0E1014"
  on-background: "#ECE7DD"
  surface-variant: "#21252D"
typography:
  display-lg:
    fontFamily: Bricolage Grotesque
    fontSize: 56px
    fontWeight: "500"
    lineHeight: 60px
    letterSpacing: -0.03em
  display-md:
    fontFamily: Bricolage Grotesque
    fontSize: 40px
    fontWeight: "500"
    lineHeight: 44px
    letterSpacing: -0.02em
  display-sm:
    fontFamily: Bricolage Grotesque
    fontSize: 28px
    fontWeight: "500"
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Pretendard Variable
    fontSize: 24px
    fontWeight: "600"
    lineHeight: 30px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Pretendard Variable
    fontSize: 20px
    fontWeight: "600"
    lineHeight: 26px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Pretendard Variable
    fontSize: 16px
    fontWeight: "600"
    lineHeight: 22px
  body-lg:
    fontFamily: Pretendard Variable
    fontSize: 16px
    fontWeight: "400"
    lineHeight: 24px
  body-md:
    fontFamily: Pretendard Variable
    fontSize: 14px
    fontWeight: "400"
    lineHeight: 20px
  body-sm:
    fontFamily: Pretendard Variable
    fontSize: 12px
    fontWeight: "400"
    lineHeight: 16px
  label-lg:
    fontFamily: Pretendard Variable
    fontSize: 14px
    fontWeight: "600"
    lineHeight: 20px
    letterSpacing: 0.02em
  label-md:
    fontFamily: Pretendard Variable
    fontSize: 12px
    fontWeight: "600"
    lineHeight: 16px
    letterSpacing: 0.04em
  label-sm:
    fontFamily: Pretendard Variable
    fontSize: 11px
    fontWeight: "600"
    lineHeight: 14px
    letterSpacing: 0.06em
  data-lg:
    fontFamily: JetBrains Mono
    fontSize: 22px
    fontWeight: "500"
    lineHeight: 28px
    letterSpacing: 0
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: "500"
    lineHeight: 20px
    letterSpacing: 0
  data-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: "400"
    lineHeight: 16px
    letterSpacing: 0
rounded:
  none: 0
  sm: 4px
  DEFAULT: 8px
  md: 10px
  lg: 14px
  xl: 20px
  full: 9999px
spacing:
  unit: 4px
  page-margin: 20px
  card-padding: 20px
  card-gap: 16px
  section-margin: 32px
  fab-offset: 24px
components:
  page-frame:
    backgroundColor: "{colors.background}"
    textColor: "{colors.on-background}"
    padding: 20px
  header-bar:
    backgroundColor: transparent
    textColor: "{colors.on-surface}"
    typography: "{typography.display-sm}"
    padding: 24px 0
  poll-pulse-active:
    backgroundColor: "#B8422E"
  poll-pulse-idle:
    backgroundColor: "{colors.outline-variant}"
  watch-card:
    backgroundColor: "{colors.surface-container}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.lg}"
    padding: 20px
    border: "1px solid {colors.outline-variant}"
  watch-card-inactive:
    backgroundColor: "{colors.surface-container-low}"
    textColor: "{colors.on-surface-variant}"
  watch-card-stripe-korail:
    backgroundColor: "{colors.secondary}"
    height: 3px
  watch-card-stripe-srt:
    backgroundColor: "{colors.tertiary}"
    height: 3px
  route-station:
    typography: "{typography.headline-md}"
    textColor: "{colors.on-surface}"
  route-arrow:
    typography: "{typography.headline-md}"
    textColor: "{colors.on-surface-variant}"
  card-date:
    typography: "{typography.display-md}"
    textColor: "{colors.on-surface}"
  card-time-range:
    typography: "{typography.data-md}"
    textColor: "{colors.on-surface}"
  card-meta-label:
    typography: "{typography.label-md}"
    textColor: "{colors.on-surface-variant}"
  badge-korail:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.sm}"
    padding: 3px 8px
  badge-srt:
    backgroundColor: "{colors.tertiary-container}"
    textColor: "{colors.on-tertiary-container}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.sm}"
    padding: 3px 8px
  badge-train-type:
    backgroundColor: "{colors.surface-container-high}"
    textColor: "{colors.on-surface-variant}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.sm}"
    padding: 3px 8px
  divider-dotted:
    backgroundColor: transparent
    border: "1px dashed {colors.outline-variant}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.full}"
    height: 48px
    padding: 0 24px
  button-primary-pressed:
    backgroundColor: "{colors.primary-fixed-dim}"
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.on-surface}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.full}"
    height: 48px
    padding: 0 20px
    border: "1px solid {colors.outline-variant}"
  button-danger-ghost:
    backgroundColor: transparent
    textColor: "{colors.error}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.full}"
    height: 36px
    padding: 0 16px
    border: "1px solid {colors.outline-variant}"
  fab:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.full}"
    height: 56px
    padding: 0 28px
  input-field:
    backgroundColor: "{colors.surface-container-low}"
    textColor: "{colors.on-surface}"
    typography: "{typography.body-lg}"
    rounded: "{rounded.md}"
    padding: 0 16px
    height: 48px
    border: "1px solid {colors.outline-variant}"
  input-field-focus:
    border: "1px solid {colors.primary}"
  input-label:
    typography: "{typography.label-md}"
    textColor: "{colors.on-surface-variant}"
  toggle-track-on:
    backgroundColor: "{colors.primary}"
    rounded: "{rounded.full}"
    width: 40px
    height: 22px
  toggle-track-off:
    backgroundColor: "{colors.surface-container-highest}"
    rounded: "{rounded.full}"
    width: 40px
    height: 22px
  toggle-thumb:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.full}"
    width: 18px
    height: 18px
  bottom-sheet:
    backgroundColor: "{colors.surface-container-high}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.xl}"
    padding: 24px
    border: "1px solid {colors.outline}"
  scrim:
    backgroundColor: rgba(8, 9, 12, 0.65)
  empty-state-line:
    backgroundColor: transparent
    border: "1px dashed {colors.outline-variant}"
  empty-state-text:
    typography: "{typography.body-lg}"
    textColor: "{colors.on-surface-variant}"
---

# 발권창구 — KTX/SRT Watcher PWA

A design system for the configuration UI of an unattended seat-availability watcher. The shell users interact with is small and infrequent — they open it to add or modify a route, then close it. The actual notifications happen elsewhere (Telegram). This UI is therefore the *waiting room*, not the *destination*: it should feel composed, slightly weathered, and never demanding of attention.

## Brand & Style

**Concept**: a printed timetable meets a station departure board. Calm, reduced, slightly weathered — the gravitas of a place where people wait. The interface is a single sheet of warm paper held at low light.

The aesthetic prioritizes **legibility over decoration, rhythm over density, typography over color**. Provider identity is the only chromatic statement on the canvas: Korail's cobalt blue and SRT's carmine, used as stamps rather than themes. Everything else is bone, graphite, and the warm dark of a platform after the last train.

**Personality**: editorial restraint, not minimalist coldness. The user is checking on a small but consequential thing — whether they can be home for dinner — and the interface should match that quiet practicality.

## Colors

A warm dark with a single chromatic accent per provider.

- **Canvas** `surface` `#0E1014` — graphite with a faint blue undertone, like the sky 10 minutes after sunset on a clear evening at Seoul Station. All on-canvas surfaces lift from this through opacity and the `surface-container-*` ladder, never through dramatic shadow.
- **Text** `on-surface` `#ECE7DD` (warm bone) — never pure white, which would feel sterile against the warm dark. Secondary text drops to `on-surface-variant` `#9C9588`, a quiet cigarette-ash gray.
- **Provider stamps**: Korail cobalt (`secondary` `#5BA8E5`) and SRT carmine (`tertiary` `#E26B7A`) — lifted from their official brand hues to maintain WCAG AA against the dark canvas. They appear *only* as: the 3px stripe at the top of a watch card, the filled badge next to a route heading, and the focus ring of an input that targets that provider. Never as background fills covering large surface area, never on body text.
- **Accent ember** `#B8422E` (Boston Clay) is reserved for *exactly one* moment: the active "now polling" pulse in the header. Used elsewhere it loses its weight. Hardcoded in `poll-pulse-active` rather than aliased so designers can't accidentally reuse it.
- **Avoid**: gradients (incompatible with editorial restraint), purple/teal (cliché dark-mode app palette), any color outside the three-axis system.

## Typography

Three families, each with a distinct job.

- **Bricolage Grotesque** (display 28–56px) — section headings, the date on each watch card. A variable grotesque with optical sizing; at large weights it has the inked feel of broadsheet headlines.
- **Pretendard Variable** (body, headline, label) — Korean-first; handles the Korean/Latin mix elegantly. Bricolage and Pretendard share a similar geometric DNA, so they pair without dissonance.
- **JetBrains Mono** (data 12–22px) — train numbers, departure times, seat counts, watch IDs. Anything that benefits from column alignment goes here.

Hierarchy uses size and weight, never color, to communicate importance. Tracking is tightened on display sizes (-0.02em to -0.03em) to compensate for optical looseness; mono uses zero tracking. Korean labels never get tracking widened — it makes 한글 look broken.

## Layout & Spacing

A 4px base unit. Page margin is 20px on mobile (the priority device — this is iOS PWA), expanding to 32–48px on tablet+.

The list is the layout. Watches stack vertically as cards with 16px gutters. No grids of small cards; vertical rhythm matters more than horizontal compression.

Asymmetry is intentional: the provider stripe sits at the top of every card; route info reads left-aligned; departure time range is right-aligned in mono, like a column in a printed timetable. The eye learns to scan two channels.

Negative space at top and bottom of the page is generous — at least 32px before the first card. The bottom of the page reserves 80px clearance for the floating "+" action.

## Elevation & Depth

Depth comes from the `surface-container-*` ladder plus a 1px `outline-variant` border, never from box-shadow on cards. The only shadow in the system is on the floating action button (`fab`): `0 8px 24px rgba(0,0,0,0.4)`, used to lift it out of the page.

The provider stripe (3px tall) sits flush at the top edge of every card and acts as both a visual marker and a subtle elevation cue — it reads as a colored binding, like the edge of a printed receipt.

The bottom sheet uses `surface-container-high` and a 1px `outline` (one tier stronger than card outline) to register as "above" the page. The scrim behind it is `rgba(8,9,12,0.65)`, dark enough to mute the page but not so dark that the back-page disappears.

## Shapes

- **Cards**: 14px radius (`rounded.lg`) — softer than 8px (which feels like a Material chip) and harder than 20px (which feels like an iOS sheet).
- **Buttons & FAB**: full-pill (`rounded.full`).
- **Inputs**: 10px (`rounded.md`).
- **Provider stripe**: hard 90° corners — it should feel applied, like a strip of tape.
- **Badges**: 4px (`rounded.sm`) — small enough to feel stamped, not bubbled.

## Components

### Watch card

Each watch is rendered as a vertical card. Anatomy from top to bottom:

1. **Provider stripe** — 3px tall colored bar across the top edge (Korail blue or SRT carmine). Hard corners, flush to the card edge.
2. **Provider badge + route line** — left-aligned. Badge first (`KORAIL` or `SRT` in `label-sm`), then a 8px gap, then the route as `headline-md`: `수원 → 부산`. Arrow uses `route-arrow` color (one tier muted).
3. **Date** — `display-md` Bricolage, anchored beneath the route. The largest type on the card. Format: `2026.05.15` with periods (Korean date-style), not `2026-05-15`.
4. **Time-range row** — labeled, right-aligned in mono `data-md`: `09:00 – 12:00`. The label `시간대` uses `card-meta-label`.
5. **Train types row** — small `badge-train-type` chips (e.g. `KTX`, `KTX-산천`), wrapped if many.
6. **Footer row** — `last_check` timestamp on the left in `label-sm` (`마지막 확인 · 2분 전`), toggle switch on the right.

Inactive watches use `watch-card-inactive` (one tier dimmer surface) and drop primary text to `on-surface-variant`. Still legible, just demoted.

### Header

Site-name `발권창구` on the left in `display-sm`. On the right, a 6px circle that pulses in `poll-pulse-active` `#B8422E` when polling is recent (< 15 min based on `state.json::last_run`), or holds in `poll-pulse-idle` (`outline-variant`) otherwise. Pulse animation: `1s ease-in-out infinite alternate` between alpha 0.4 and 1.0.

Below the title, a hairline `divider-dotted` separates the header from the list — a 1px dashed line running the full content width.

### Add-watch sheet

Bottom sheet on mobile (88% viewport height with 24px top padding for grab handle), centered modal at 480px width on tablet+. The form sections follow watch-card anatomy: Provider → Route → Date → Time range → Trains → Passengers → Seat class. Submit button is a full-width `button-primary` pill, sticky at the bottom of the sheet inside the safe area.

### Empty state

If no watches exist, render two parallel `divider-dotted` lines running horizontally across the screen with 64px gap between them — abstract train tracks. Centered between them: `empty-state-text` reading `아직 워치가 없습니다`. Below, a `button-primary` for "첫 워치 추가하기".

### Floating "+" action

`fab` pill, sticky 24px from the bottom-right edge respecting `safe-area-inset-bottom`. Label `+ 워치 추가`. On tap, opens the add-watch sheet with a 200ms ease-out slide.

### First-run setup

If the GitHub PAT is missing from localStorage, the entire page is replaced with a single centered card containing: `headline-lg` "GitHub 연결", `body-lg` brief explainer, an `input-field` for the PAT, and a `button-primary` "연결". No nav, no header pulse — the watcher is offline until linked.

## Don'ts

- Don't add gradients to backgrounds or buttons. The aesthetic is print-flat.
- Don't use Korail blue or SRT carmine on text. Only stripes, badges, and focus rings.
- Don't shorten Korean station names to abbreviations (수원 → 수). Always full names.
- Don't render times in proportional fonts. Always mono. `09:35 발`, not `9:35 발`.
- Don't apply shadows to cards. Use the surface-container ladder.
- Don't use emoji as primary iconography. The 🚄 in Telegram alerts is intentional informality for a notification context — the PWA configuration UI uses no emoji and no decorative icons.
- Don't introduce a new accent color. The two providers and the ember pulse are the entire chromatic vocabulary.

## Do's

- Lead with route and date. Time range is supporting info.
- Use the mono font for all numbers without exception (departure times, train numbers, seat counts, dates if displayed in YYYY-MM-DD form, IDs).
- Reserve `accent-ember` (`#B8422E`) for the polling-status pulse only.
- Maintain the 4px rhythm everywhere — paddings, gaps, gutters, and font sizes (where reasonable) all multiples of 4.
- Test on a 375px-wide viewport (iPhone SE) before tablet/desktop. The PWA's primary surface is the home-screen icon, not a desktop browser.
- When in doubt about hierarchy, remove a level rather than add one.
