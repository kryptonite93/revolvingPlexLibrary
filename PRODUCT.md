# Product

## Register

product

## Platform

web

## Users

The primary user is a technically capable Plex server owner administering a private Unraid deployment. They need to understand library state quickly, verify why media is protected or eligible, and progressively enable automation without surrendering control. Human maintainers are a secondary audience for audit and recovery workflows.

## Product Purpose

Revolving Plex Manager coordinates Plex, Overseerr, Tautulli, Radarr, Sonarr, and qBittorrent to operate a playback-aware and seed-aware revolving media library. Success means every lifecycle decision is explainable, ambiguous state fails closed, existing media remains protected during rollout, and approved deletion workflows converge safely across all connected systems.

Phase 1 targets Overseerr 1.34.0. Request-system behavior lives behind a provider-neutral adapter so a later move to Seerr does not leak into lifecycle policy or persistence.

## Positioning

It is the safety and lifecycle control layer between media activity, request history, Arr inventory, and torrent obligations—not a replacement for any of those services.

## Brand Personality

Plex-inspired, media-forward, and operationally trustworthy. The interface should feel at home beside established media-server tools while remaining calmer and more explicit around destructive state.

## Anti-references

Avoid a generic enterprise dashboard, a graph-heavy monitoring console, ornamental glass effects, and interfaces that hide critical state behind icons or color alone. Destructive controls must never feel casual or visually equivalent to routine actions.

## Design Principles

- Make safety state visible before presenting action.
- Explain decisions in plain language and preserve the underlying evidence.
- Use familiar media-library patterns without imitating Plex branding.
- Reveal complexity progressively while keeping expert detail available.
- Treat empty, stale, ambiguous, and failed states as first-class product states.

## Accessibility & Inclusion

Use WCAG 2.2 AA as an engineering baseline where practical: keyboard-operable controls, visible focus, reduced-motion support, sufficient contrast, semantic structure, and state communication that does not rely on color alone.
