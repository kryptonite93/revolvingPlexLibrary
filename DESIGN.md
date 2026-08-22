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

**The Amber Is Earned Rule.** Use warm amber for selection, current rollout position, pending approval, unsaved forms, and stale calculations—not as ambient decoration. Current Save and Recalculate actions remain neutral; destructive execution remains red.

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

Rollout Control pairs a three-stop stage track with one compact confirmation form. The track and form share a row on wide screens, stack below 1100px, and the track turns vertical on phones. A manual approval case opens with title identity and a four-part safety ledger, then divides the next safe action from its chronological execution trail. Those columns stack below 1100px; the ledger reduces to two columns below 880px and one column on phones, where each trail event also becomes a single-column record.

Manual Management uses a filter-first workbench: Overseerr user, target Arr instance, and tracker conditions form a compact header before any result or selection control. Result and series-summary rows hold four aligned columns—selection, media identity, request context, and tracker state—through 780px; below that breakpoint they become two-column records with metadata stacked under the identity and no horizontal overflow. Its desktop selection bar stays sticky at the top of the workspace, then becomes static below the compact-shell breakpoint and single-column on phones.

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

The detail surface enlarges the media spine and verdict, then presents a four-stop timeline for original import, meaningful watch, retention deadline, and protection. Files and revisions, torrent mapping, playback evidence, and request protection use paired divider-led evidence ledgers; a source-freshness ledger closes the case file. Meaningful playback places the Tautulli or Plex user identity beside its timestamp so the evidence remains attributable. Torrent headings state how many torrents link to the current title. When a torrent also maps elsewhere, a compact warning names and links every other lifecycle so a shared-torrent block can be verified directly. Unknown, unmapped, and unavailable evidence is written in place and explains when eligibility is blocked.

### Manual Management Workbench

Manual Management is a requester-owned, cross-instance operational workbench reached from primary navigation. Three explicit filters—Overseerr user, target Radarr or Sonarr instance, and tracker conditions—precede the result set. Movie requests render as direct compact rows. TV requests render as native disclosure rows whose expanded children are independently selectable seasons; season zero is always written as **Specials**, while the series itself remains in Sonarr.

The sticky desktop selection bar keeps the exact selected count, per-page choices, a destructive review action, and select-all across every filtered page in one place. Selecting all filtered results disables individual checkboxes to make the selection scope unambiguous. Protection and retention do not silently defeat an explicit manual selection; identity, active playback, tracker, shared-torrent, and freshness checks still fail closed immediately before mutation.

One modal summarizes the destructive batch and uses one acknowledgment checkbox. Radarr exposes a default-on import-exclusion choice; Sonarr instead explains why exclusions cannot be applied at season scope. The latest batch result remains above the workbench and exposes Retry whenever unfinished work exists, including a pending batch whose execution never started.

### Integration Setup

Each service row combines a 2:3 media tile, identity and URL, stacked text states, routine actions, and a progressively disclosed configuration region. Read-only enablement, management mode, and Active Management are visibly separate concepts. Active Management remains locked until its prerequisites are met, and enabling it requires consequence copy plus an explicit confirmation when available.

Radarr and Sonarr rows place one compact **Plex library pairing** control before management mode. Its default “Detect automatically” option compares exact, case-insensitive filenames from active Arr lifecycles with enabled Plex libraries of the compatible media type during Plex sync. Automation accepts only an unambiguous winner for both the candidate library and competing Arr instances; zero matches and ties remain pending. Once validated, an automatic pairing remains authoritative when a later sync proves the library empty, so absence can reconcile without erasing its scope; only new unique evidence or a manual choice can replace it. A manual library choice is the fallback for ambiguous installations, is labeled as manual, claims that library for one Arr instance, and is never overwritten by later automatic detection.

The helper line carries the complete state in words: **auto** names successful filename detection, **manual** says that detection will not override the choice, and **pending** directs the owner to sync Plex or first select a compatible Plex library. Pending and mismatched states fail closed: destructive execution remains blocked until the Arr instance has a pairing and the lifecycle has been synchronized into that currently paired library. Returning to automatic detection clears stale lifecycle-to-Plex associations until fresh evidence establishes the pairing again.

The add-service panel remains a single sticky form on wide screens and moves before the service list on narrower layouts. New services are presented as disabled by default, credential handling is explained before entry, and empty or unavailable operations teach the next safe step.

### Tracker Rules

Tracker rules lead the Settings safety story, but discovered domains do not expand into configuration records automatically. Add trackers opens a focused, searchable selection dialog with Select shown and Clear actions; only selected domains appear in the main Settings flow. Each selected qBittorrent tracker appears as a divider-led record with its observed torrent count, written policy state, ratio and whole-day seed-time requirements, grace period, and a tracker-scoped future-automation gate. Rules save individually beside the tracker identity. Save actions remain neutral while current, then turn amber and use explicit unsaved copy after a field changes. Unselected domains remain fail-closed and block cleanup. Requirement choices disable fields that do not participate in the selected rule, and Never remove suppresses every cleanup-permission input while explaining why.

### Read-only Deletion Preview

The preview lives on the dedicated Deletion Queue page and evaluates stored evidence without contacting an integration. A compact five-part summary separates eligible-in-simulation, total eligible storage, blocked, retained, and protected results. The four decision counts are also filters, allowing small blocked or retained sets to be inspected without scanning thousands of eligible items; each result names its reason and carries a persistent View evidence cue into the lifecycle case file. Recalculate remains neutral when results match stored evidence and turns amber only after a relevant sync, protection, or policy change. Inventory Only remains visible in the page header and timestamp line, so an eligible simulation is never presented as executable.

An Arr item with no torrent currently associated in qBittorrent treats tracker obligations as not applicable and may remain eligible based on retention and protection evidence. Once a torrent association exists, its mapping confidence, completion state, tracker policy, ratio, and seed time become mandatory gates.

Shared torrents, including a future multi-season pack, fail closed when one torrent maps to more than one movie or season. The preview names the other mapped titles, and each lifecycle case file exposes the complete reverse mapping before automatic cleanup remains blocked. A dedicated mapping workflow must be implemented and tested before that block can be overridden.

### Staged Rollout Control

Rollout Control presents Inventory Only, Dry Run, and Approval Required as a written three-stop safety sequence rather than an unlabeled toggle. Only the current stop receives an amber node; every other stop stays neutral. The adjacent small-radius form explains the selected mode, requires an explicit confirmation for a rollout change, permits immediate retreat to a safer stage, and prevents skipping directly from Inventory Only to Approval Required. Advancing the rollout only reveals the next controls—it never approves a title or starts execution.

### Manual Approval Case / Execution Trail

The queue lists approval cases as flat divider-led rows with title, preparation time, written job state, and current checkpoint. Each title-specific case repeats identity, integration, media type, size, state, and correlation identifier before a four-part safety ledger for rollout, Radarr scope, stored decision, and external-change status. The lower split view keeps one next safe action beside an ordered, immutable execution trail so recovery state is visible before any retry.

Preparation remains app-local. The title-specific execution form is the only destructive confirmation: one required checkbox sits beside a red **Delete movie** action, with no typed title, `DELETE [title]` phrase, or separate approval step. Its warning names the movie and files, discloses that Radarr creates or verifies an import exclusion so synchronized lists do not restore it, and explains that a deliberate Overseerr request can add it again. Mapped torrent data may be removed only after Radarr succeeds.

Execution revalidates evidence before mutation, requires both Radarr deletion and import-exclusion confirmation before qBittorrent cleanup, and resumes from saved checkpoints without repeating confirmed work. Resume controls cover `REVALIDATED` as well as the later Radarr, torrent, and Plex checkpoints. Reconciliation may verify or repair the import exclusion before refreshing the currently paired Plex library; it waits only while the title remains present there and never deletes another media item or torrent. Amber marks reconciliation, red is reserved for deletion or resume, and cancellation stays quiet.

Completion confirms Radarr absence, torrent handling, and absence from the paired Plex library. Overseerr availability is observed after that refresh but is neither a completion gate nor a mutation target. When Overseerr still reports stale availability—or cannot be read—the completed panel appends an explicit review warning and states that no Overseerr record was changed.

## Do's and Don'ts

### Do:

- **Do** show safety state and prerequisites before presenting consequential actions.
- **Do** use lists and split detail regions for dense operational information.
- **Do** keep health, enablement, management mode, and Active Management textually distinct.
- **Do** use poster-like media geometry to establish context without obscuring URLs, health, or controls.
- **Do** order lifecycle evidence from source freshness through import, watch, retention, protection, and torrent mapping before future action.
- **Do** treat each media title as a traceable case file with explicit unknown, stale, blocked, and empty states.
- **Do** state whether each Arr-to-Plex pairing was detected automatically, set manually, or is still pending, with the next safe step beside the control.
- **Do** keep preparation, the single destructive confirmation, resume, and reconciliation as explicitly named title-scoped states with a persistent correlation identifier and immutable checkpoint trail.
- **Do** keep manual selection scope, selected count, and cross-page select-all visible before a destructive batch is reviewed.
- **Do** use native disclosure for selectable TV seasons and label season zero as Specials in every user-facing context.
- **Do** preserve visible labels, local validation, keyboard focus, and reduced-motion behavior.

### Don't:

- **Don't** hide critical state behind an icon, dot, or color alone.
- **Don't** make destructive controls look equivalent to routine connection or read-only actions.
- **Don't** turn repeated service records into a generic grid of floating cards.
- **Don't** collapse lifecycle evidence into a poster grid, a single opaque score, or a color-only verdict.
- **Don't** infer an Arr-to-Plex pairing from titles, identifiers, or folder similarity, or allow ambiguous and unsynchronized pairing evidence to reach deletion.
- **Don't** add a typed title, `DELETE` phrase, or separate approval action to manual movie deletion; the title-specific checkbox is the explicit gate.
- **Don't** let protection or retention silently override explicit manual selection, or let manual intent bypass fresh identity, playback, tracker, and shared-torrent checks.
- **Don't** describe a season-scoped Sonarr action as an exclusion or show Specials as Season 0.
- **Don't** let resume or reconciliation obscure completed checkpoints or delete another media item or torrent.
- **Don't** use ornamental glass, decorative page-load choreography, or graph-heavy console styling.
- **Don't** expose saved credentials back to the browser.
