---
name: Revolving Plex Manager
description: A calm, media-forward control surface for safe revolving-library operations.
colors:
  midnight-canvas: "oklch(0.115 0.008 260)"
  media-surface: "oklch(0.165 0.01 260)"
  raised-surface: "oklch(0.205 0.012 260)"
  quiet-divider: "oklch(0.335 0.012 260)"
  control-outline: "oklch(0.52 0.012 260)"
  warm-ink: "oklch(0.955 0.005 80)"
  cool-muted: "oklch(0.735 0.012 260)"
  safety-amber: "oklch(0.784 0.144 79.8)"
  action-amber: "oklch(0.48 0.14 76)"
  focus-cyan: "oklch(0.77 0.125 220)"
  healthy-green: "oklch(0.7 0.145 150)"
  danger-red: "oklch(0.635 0.19 28)"
typography:
  display:
    fontFamily: "Segoe UI Variable, Aptos, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "clamp(2rem, 4vw, 3.8rem)"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Segoe UI Variable, Aptos, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "2.35rem"
    fontWeight: 700
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Segoe UI Variable, Aptos, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "1rem"
  label:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "0.74rem"
    fontWeight: 680
rounded:
  sm: "0.45rem"
  md: "0.8rem"
  pill: "999px"
spacing:
  xs: "0.45rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.action-amber}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "0.7rem 1rem"
    height: "2.75rem"
  button-secondary:
    backgroundColor: "{colors.raised-surface}"
    textColor: "{colors.warm-ink}"
    rounded: "{rounded.sm}"
    padding: "0.7rem 1rem"
    height: "2.75rem"
  input:
    backgroundColor: "{colors.midnight-canvas}"
    textColor: "{colors.warm-ink}"
    rounded: "{rounded.sm}"
    padding: "0.8rem 0.9rem"
  status-pill:
    backgroundColor: "transparent"
    textColor: "{colors.cool-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.45rem 0.7rem"
---

# Design System: Revolving Plex Manager

## Overview

**Creative North Star: "The Safety-Lit Media Room"**

Revolving Plex Manager belongs beside familiar media-server tools in a dim home office or media room, but it is calmer and more explicit around consequential operations. Near-black surfaces, restrained tonal depth, poster-like geometry, and sparing amber cues create a media-forward identity without imitating Plex branding.

Operational understanding always outranks decoration. Every title is treated as a lifecycle case file rather than another poster in a generic media grid: source freshness, import history, meaningful playback, retention, protection, and torrent evidence are traced before any verdict or future action. The first viewport establishes whether the evidence can be trusted, then leads into a chronological workbench. Dense service information remains legible, important state is written out, and progressively disclosed controls reveal complexity only when the owner asks for it.

The lifecycle workbench is the signature expression of Direction B, anchored by concept seed `c0c31952`. It extends the same near-black surfaces, poster geometry, restrained amber selection, and explicit text states already used by integrations; it does not introduce a separate dashboard language. The interface refuses generic metric-card dashboards, ornamental glass effects, and graph-heavy monitoring-console styling.

**Key Characteristics:**

- Dark, restrained media-control surfaces.
- Poster-proportioned imagery and service marks used as context, not content replacement.
- Safety state and prerequisites visible before action.
- Source freshness and chronological evidence visible before verdicts.
- Amber reserved for selection, progress, and primary emphasis.
- Compact, text-forward operational density.

## Colors

The palette is a near-black blue-neutral field with warm ink, tightly rationed amber, a cool focus cue, and explicit semantic health colors.

### Primary

- **Safety Amber:** Marks selection, progress, protected state, and the strongest routine emphasis; its rarity gives it authority.
- **Action Amber:** Provides the deeper fill for primary actions so controls remain readable against the dark canvas.

### Secondary

- **Focus Cyan:** Creates a distinct keyboard-focus signal without competing with amber's product meaning.

### Tertiary

- **Healthy Green:** Communicates healthy, enabled, and managed states alongside explicit text.
- **Danger Red:** Is reserved for errors, destructive-action gates, and their confirmations.

### Neutral

- **Midnight Canvas:** The page-level ground.
- **Media Surface:** The default sidebar, panel, and expanded-control surface.
- **Raised Surface:** Interactive and elevated neutral regions.
- **Quiet Divider / Control Outline:** Separate information and controls without turning the interface into a box grid.
- **Warm Ink / Cool Muted:** Establish the primary and supporting text hierarchy.

### Named Rules

**The Amber Is Earned Rule.** Use amber for selection, progress, protected state, and primary emphasis—not as ambient decoration.

**The Text Carries State Rule.** Color and dots may reinforce a state, but a visible text label must communicate it.

## Typography

**Display Font:** Segoe UI Variable, with Aptos and the system UI stack as fallbacks  
**Body Font:** Segoe UI Variable, with Aptos and the system UI stack as fallbacks  
**Label/Mono Font:** The system monospace stack

**Character:** The system stack is compact, dependable, and native to an operational tool. Tight heading tracking adds confidence; monospaced labels distinguish rollout and machine-oriented state from narrative copy.

### Hierarchy

- **Display:** Bold, tightly tracked, and compact for the primary operational status field.
- **Headline:** Confident page titles that remain subordinate to the current state and next safe action.
- **Title:** Compact service and section labels, usually around the base text size with stronger weight.
- **Body:** Readable system UI copy; explanations stay near 65–75 characters per line where the layout permits.
- **Label:** Small, strong labels for status pills, reason codes, timestamps, identifiers, and supporting control metadata.

### Named Rules

**The Plain-Language First Rule.** Lead with the human-readable decision or prerequisite; identifiers and machine detail support it rather than replace it.

## Layout

Desktop uses a persistent 15.5rem sidebar and a bounded workspace with generous responsive padding. Operational pages favor lists, timelines, evidence ledgers, and split detail views over repeated card grids. The integration setup uses a dense service list beside a single sticky connection panel; below 1100px the form moves above the list, and below 520px service summaries and control groups become single-column.

The media surface opens with a source-freshness ribbon, followed by a collapsed policy drawer and a chronological lifecycle workbench ordered by retention deadline. Each desktop row aligns title identity, a three-stop evidence track, and a textual verdict. At 1100px the verdict moves beneath the identity and track; at phone widths the track becomes vertical. The detail view expands the same grammar into a four-stop lifecycle timeline, a two-column evidence ledger, and a source-freshness footer; timeline and evidence columns collapse progressively to one column.

Spacing follows a compact 0.45rem–2rem rhythm. Repeated rows are separated by quiet dividers, not individual card shells. Expanded service configuration aligns explanatory copy on the left and the active control on the right, collapsing to a natural reading order on small screens. Policy controls stay secondary inside a disclosure and preserve the same left-to-right reading order before stacking.

**The One Active Form Rule.** Keep one prominent creation or connection form in view while the surrounding list supplies context and management actions.

**The Evidence Before Verdict Rule.** Establish freshness and chronology before presenting a lifecycle decision; the user should be able to trace the verdict in reading order.

## Elevation & Depth

The system combines tonal layering with restrained ambient shadows. Most hierarchy comes from the canvas, surface, and raised-surface steps; shadows are reserved for the large readiness surface, sticky connection panel, and small poster- or spine-proportioned tiles that need to read as media objects. Lifecycle timelines remain flat and use a one-pixel track plus nodes, keeping chronology structural rather than decorative.

### Shadow Vocabulary

- **Ambient Panel:** A broad, low-contrast shadow for a major raised operational surface.
- **Media Tile:** A tighter shadow under poster-like service tiles.
- **Media Spine:** A directional shadow offset to the right and below the narrow title marker, giving it the weight of a physical case spine.

### Named Rules

**The Tonal-First Rule.** Establish hierarchy with surface color and dividers before adding shadow.

## Shapes

Controls and media tiles use gently curved small corners; major bounded surfaces may use the medium radius. Pills are fully rounded only for compact status summaries. Service tiles use a 2:3 poster silhouette, linking integrations to the media world without relying on third-party logos. Lifecycle title markers compress that poster language into narrow case spines with a slightly tighter leading edge; warm movie spines and cool season spines remain subordinate to the written media type. Timeline nodes are circular, but the surrounding rows and evidence sections stay rectilinear and divider-led. Form groups and dense expanded controls use borders and straight-edged regions rather than ornamental containers.

## Components

Components feel compact, deliberate, and explicit about consequence.

### Buttons

- **Shape:** Gently curved with a consistent minimum height and sturdy weight.
- **Primary:** Deep amber fill with white text for the main routine action.
- **Secondary:** Raised neutral fill and a visible outline for routine operations.
- **Quiet:** Transparent, full-width where contextual, and visually subordinate.
- **Danger:** Red-tinted fill and border only for a destructive capability gate or destructive action.
- **Hover / Focus:** Short state transitions, a one-pixel active press, and a high-visibility cyan focus outline. Disabled buttons remain readable and include adjacent prerequisite copy where the reason is not obvious.

### Cards / Containers

- **Corner Style:** Small corners for media tiles; medium corners only for major bounded surfaces.
- **Background:** Tonal layering uses the media and raised surfaces.
- **Shadow Strategy:** Ambient and selective; repeated list rows remain flat.
- **Border:** Quiet dividers separate dense operational records.
- **Internal Padding:** Compact for rows, roomier for the sticky connection panel and expanded controls.

### Inputs / Fields

- **Style:** Visible labels, midnight-canvas fill, control outline, small-radius corners, and adjacent help or validation.
- **Focus:** Amber border plus a translucent amber halo, supplemented by the global cyan focus-visible outline.
- **Error / Disabled:** Error text is explicit and local. Credential fields are write-only; saved secrets never return to the browser.
- **Alternatives:** Related credential paths may be grouped in a plain bordered fieldset with a visible legend.

### Navigation

The sidebar is persistent and restrained on desktop. The active destination combines readable text, a lightly tinted amber field, and a small amber marker. At narrow widths navigation becomes an overflow-safe horizontal row, then a two-column grid on phones.

### Status Labels

State labels pair explicit text with a small dot. Disabled, unhealthy, enabled, protected, ignored, and managed remain distinguishable in words; color is supplemental. The global rollout summary uses a bordered monospaced pill.

### Source Freshness Ribbon

The first media viewport pairs an “Evidence clock” explanation with one compact cue per inventory source. Each cue uses a divider, a small health dot, the source name, a written freshness state, and the last successful synchronization time. Missing synchronization is stated as “Never synchronized”; stale or unknown evidence is never softened into a neutral decorative state.

### Lifecycle Workbench

The signature lifecycle row is a chronological workbench, not a media card. A narrow movie or season spine leads into title and source identity, followed by a connected three-stop track for import, last meaningful watch, and retention deadline. The final column names the decision and summarizes protection plus torrent-mapping evidence. Rows are ordered by retention deadline, open a read-only case file, and use amber only for the track nodes, protected emphasis, and title hover.

The empty workbench names the missing evidence source and points to integrations without implying that content can be inferred from folder names. Policy fields remain in a disclosure above the workbench and explicitly state that saving recalculates stored evidence without calling external mutation APIs.

### Lifecycle Case File

The detail surface enlarges the media spine and verdict, then presents a four-stop timeline for original import, meaningful watch, retention deadline, and protection. Files and revisions, torrent mapping, playback evidence, and request protection use paired divider-led evidence ledgers; a source-freshness ledger closes the case file. Unknown, unmapped, and unavailable evidence is written in place and explains when eligibility is blocked.

### Integration Setup

Each service row combines a 2:3 media tile, identity and URL, stacked text states, routine actions, and a progressively disclosed configuration region. Read-only enablement, management mode, and Active Management are visibly separate concepts. Active Management remains locked until its prerequisites are met, and enabling it requires consequence copy plus an explicit confirmation when available.

The add-service panel remains a single sticky form on wide screens and moves before the service list on narrower layouts. New services are presented as disabled by default, credential handling is explained before entry, and empty or unavailable operations teach the next safe step.

### Tracker Rules

Tracker rules lead the Settings safety story. Each qBittorrent tracker domain appears as a divider-led record with its observed torrent count, written policy state, ratio and seed-time requirements, grace period, and a tracker-scoped future-automation gate. Unconfigured domains state that they block cleanup. Requirement choices disable fields that do not participate in the selected rule, and Never remove suppresses every cleanup-permission input while explaining why.

### Read-only Deletion Preview

The preview follows tracker rules and evaluates stored evidence without contacting an integration. A compact four-part summary separates eligible-in-simulation, blocked, retained, and protected results; each result then names its reason and carries a persistent View evidence cue into the lifecycle case file. Inventory Only remains visible in the page header and in the preview timestamp line, so an eligible simulation is never presented as executable.

Shared torrents, including a future multi-season pack, fail closed when one torrent maps to more than one movie or season. The preview must name that shared mapping and block automatic cleanup until a dedicated mapping workflow is implemented and tested.

## Do's and Don'ts

### Do:

- **Do** show safety state and prerequisites before presenting consequential actions.
- **Do** use lists and split detail regions for dense operational information.
- **Do** keep health, enablement, management mode, and Active Management textually distinct.
- **Do** use poster-like media geometry to establish context without obscuring URLs, health, or controls.
- **Do** order lifecycle evidence from source freshness through import, watch, retention, protection, and torrent mapping before future action.
- **Do** treat each media title as a traceable case file with explicit unknown, stale, blocked, and empty states.
- **Do** preserve visible labels, local validation, keyboard focus, and reduced-motion behavior.

### Don't:

- **Don't** hide critical state behind an icon, dot, or color alone.
- **Don't** make destructive controls look equivalent to routine connection or read-only actions.
- **Don't** turn repeated service records into a generic grid of floating cards.
- **Don't** collapse lifecycle evidence into a poster grid, a single opaque score, or a color-only verdict.
- **Don't** use ornamental glass, decorative page-load choreography, or graph-heavy console styling.
- **Don't** expose saved credentials back to the browser.
