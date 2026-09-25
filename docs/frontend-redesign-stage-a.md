# Frontend redesign — Stage A

Visual and behavioural redesign of the React client (`frontend/`) from the Claude Design handoff
(desktop D1–D9, mobile M1–M4, design system tokens/components/breakpoints).

- **[V]** visual behaviour implemented in this stage.
- **[D]** behaviour that needs real agency data or backend support: **not** implemented (see §7).

The design is a visual reference, not a business specification. Backend, API contract, routes,
authentication, unread/acknowledge semantics, shared statuses, notes, sync and deployment are unchanged.

**Synthetic prototype data never enters production code.** Dossier numbers, names, counts, queue
hierarchy, users, session history and dates seen in the design are not copied anywhere. Every
operational value comes from the existing API. The handoff bundle and the exported HTML/PDF are
read-only references and are not copied into or committed to this repository. Tests use their own
synthetic fixtures (`src/test/utils.tsx`).

## 1. Route → design screen

| Route | Component | Design | Notes |
|---|---|---|---|
| `/login` | `pages/Login` | D1 Connexion | Same `POST /auth/login` call; brand panel + form |
| `/` | `pages/Dashboard` | D2 / M1 Accueil | Counters, unread changes, busiest queues, activity |
| `/inbox` | `pages/Inbox` → `ItemsBrowser` | D3 / M3 À traiter | Cross-queue list |
| `/workflows/:key` | `pages/WorkflowPage` → `ItemsBrowser` | D4 File | Header, status tabs, table, optional preview |
| `/dossiers/:id` | `pages/DossierPage` | D5 / M4 Dossier | Header actions, key facts, status, changes, facts, notes |
| `/admin/session` | `pages/Admin` `AdminSession` | D6 Connexion OmegaFlow | State + existing actions only |
| `/admin/health` | `AdminHealth` | D7 Suivi des lectures | Summary, state chips, table, cycles |
| `/admin/workflows` | `AdminWorkflows` | D8 Configuration des files | Grouped table over existing fields |
| `/admin/users` | `AdminUsers` | D9 Utilisateurs | Table, chips, create form |

Routing (`App.tsx`), guards (`RequireAuth`, `RequireAdmin`) and URLs are unchanged.

## 2. API data used per screen

All types come from `src/api/schema.d.ts` / `types.ts`.

| Screen | Endpoint → fields |
|---|---|
| Shell (sidebar/topbar) | `GET /workflows` → `key, name, category, enabled, unread_new, unread_changed`; `GET /dashboard` → `session.state/label/last_poll_at/last_success_at`, `counters.problem_workflows`; `GET /auth/me`; `POST /auth/logout`; `GET /dossiers/search` (global search) |
| Login | `POST /auth/login` |
| Accueil | `GET /dashboard` → `counters.{actionable_new, changed_unread, by_work_status}`, `alerts`, `activity`, `session`, `last_run`; `GET /inbox?unread=true` → `items, total`; `GET /workflows` for busiest queues |
| À traiter | `GET /inbox` → `items[*]` (`unread, unread_kind, dossier_number, insured_name, registration, garage, workflow_name, portal_status, work_status, work_version, detected_at, primary_date_raw/label, queue_age_days, omegaflow_url`), `facets`, `columns`, `total`, `page`; `POST /occurrences/{id}/acknowledge`; `PUT /memberships/{id}/work-status` |
| File | `GET /workflows/{key}` + `/workflows/{key}/items`, same item fields, `columns`, `filter_label`, `recent_events` |
| Dossier | `GET /dossiers/{id}` → `dossier`, `memberships`, `events`, `notes`; `POST /dossiers/{id}/notes`; work-status + acknowledge as above; `omegaflow_url` |
| Connexion OmegaFlow | `GET /sync` → `session`, `last_run`, `pending_sync_requests`, `outbox_pending`; `POST /sync/run`; `session.connect_url` |
| Suivi des lectures | `GET /sync` → `workflows[*]` (`last_poll_at/status, last_success_at, last_error, last_run`), `recent_runs`, `alerts` |
| Configuration | `GET /admin/workflows`, `PATCH /admin/workflows/{key}` (`enabled`, `rules_status`, `notification_class`) |
| Utilisateurs | `GET/POST /admin/users`, `PATCH /admin/users/{id}` (`active`) |

## 3. Design tokens [V]

Declared as CSS variables in `src/styles/tokens.css` on `:root` (light, default) and
`:root[data-theme="dark"]`. Names are the handoff names.

- Colour: `--bg --surface --surface-2 --surface-3 --border --border-strong --text --text-2 --text-3
  --accent --accent-hover --accent-soft --accent-text --on-accent --focus`, treatment statuses
  `--st-{todo,prog,wait,done}-{bg,fg,dot}`, markers `--new-bg/fg --mod-bg/fg`, semantic
  `--danger(-soft/-fg) --on-danger --warn(-soft/-fg) --ok(-soft)`, shadows `--shadow-1..3`.
- Type: IBM Plex Sans 400–700 (variable) and IBM Plex Mono 400/500/600, **self-hosted** from
  `src/assets/fonts` (SIL OFL 1.1, licence notice alongside), system fallback. Scale
  display 24/32·600, title 20/28, section 15/22, body 14/20, table 13/18, label 12/16·600, meta 12/16, mono 13.
- Space `--space-1..12` (4 … 48 px); radii sm 4 / md 6 / lg 8 / pill; heights: topbar 56, sidebar 256
  (rail 64), table row 52 (compact 40), control 36 (44 on touch), drawer 320.
- Focus: `:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px }`.
- Theme: `data-theme` on `<html>`; preference `light | dark | system` stored in `localStorage`
  (`rma-theme`), default **light**. `public/theme-init.js` (same origin, CSP-safe) applies it before first paint.

## 4. Shared components (as built)

`src/components/ui/`: `Button` (+ `buttonClass` for link-styled buttons), `Badges` (`TreatmentStatusPill`,
`OmegaFlowStatusTag`, `ChangeMarker`, `CountBadge`), `TreatmentStatusControl` (segmented radio group on
the dossier, styled select in lists/preview; optimistic, 409-aware, undo toast), `Filters` (`FilterChip`
quick, `FacetSelect` combinable), `SyncBanner`, `Toast` (+ provider), `States` (`EmptyState`, `ErrorState`,
`Skeleton`, delayed `DelayedSkeleton`), `ThemeSelector`, `PageHeader`, `Form` (`TextField`, `SelectField`,
`Toggle`). Icons: `lucide-react` SVG components only. Hooks: `useDebouncedValue`, `useMediaQuery`,
`useOpenItem`; `theme.tsx` (`ThemeProvider`, `useTheme`); `syncStatus.ts` (freshness from `/dashboard`).

Shell (`components/shell/`): `AppShell`, `Sidebar`, `Topbar`, `GlobalSearch`. Lists: `ItemsBrowser`,
`ItemsTable` (plain semantic table), `DossierCards` (phones), `DossierPreviewPanel`, `StatusTabs`.
Dossier: `NotesPanel`. Legacy `Badges.tsx` keeps the class/reading/rules badges and `EventList` the feed.
Admin screens live in `pages/admin/`. Styles: `styles/{tokens,base,components,shell,pages}.css`.

## 5. Responsive mapping [V]

| Width | Navigation | Lists | Dossier |
|---|---|---|---|
| ≥ 1280 | Sidebar 256 (collapsible to rail) | Full table; preview panel on File pages | 2 columns |
| 1024–1279 | Rail 64 by default, expandable | Table; File column hidden when preview open | 2 columns, notes 360 |
| 768–1023 | Drawer from menu button | Reduced table (secondary columns hidden), no preview | 1 column, sticky header |
| < 768 | Drawer 320, 44–48 px rows | `DossierCard` list | 1 column, sticky bottom action bar |

No mobile-only product behaviour is added: the sticky bar reuses existing actions (open in OmegaFlow,
change status, add note).

## 6. Vocabulary

Accueil · À traiter · Files · Connexion OmegaFlow · Suivi des lectures · Statut de traitement
(shared, editable) · Statut OmegaFlow (read-only). "À valider sur site" is no longer repeated per
queue: one page-level notice on a queue page, and full detail only in *Configuration des files*.

## 7. Deferred [D] — not implemented, not simulated

Assignment / Gestionnaire · « Attribués à moi » · « Mes dossiers en cours » · favourites/personal
queues · SLA, deadline (« Échéance », J-n) and overdue/urgency · « Tout marquer comme lu » (no bulk
endpoint) · per-queue polling frequency and « prochain cycle » · session-expiry estimate · session
history and the in-portal reconnection form (reconnection stays the existing `connect_url` /
sync actions) · per-file « Relancer » (only global sync exists) · user statistics · note editing ·
drag-reorder and extra queue settings · « Colonnes » chooser and density toggle · keyboard chords
(`g a`, `g t`) and row-level 1–4 shortcuts · AI Assistant new behaviour (the existing optional
panel is kept as is and restyled; nothing new is added) · vehicle model and old→new values in
« Ce qui a changé » (the API exposes changed field labels only).

## 8. Implementation order and commits

1. Tokens, fonts, theme, primitives + this document → `feat(frontend): design tokens, themes and primitives`
2. AppShell, Sidebar, Topbar, global search → `feat(frontend): shared application shell`
3. Login, Accueil → `feat(frontend): login and Accueil redesign`
4. À traiter and File (table, cards, preview) → `feat(frontend): queue and workflow screens`
5. Dossier → `feat(frontend): dossier screen`
6. Session, health, configuration, users → `feat(frontend): session and administration screens`
7. Responsive/accessibility polish, docs update → `feat(frontend): responsive and accessibility refinements`

## 9. Decisions taken while building

- The optional AI panel is kept exactly as it was (restyled only); no disabled « Bientôt disponible » entry was added because a working component already exists.
- Treatment status stays editable from lists (styled select) as before; the preview panel is opened with an explicit eye button so that a row click keeps meaning « open and acknowledge ».
- The dossier keeps its file tabs even with one file: choosing a tab is the explicit open that acknowledges (existing rule).
- Employees see no sync alerts or reading details: the neutral banner only. Administrators get the alert with « Reconnecter » and the « Détails de lecture » disclosure on queue pages.
- « Ce qui a changé » lists recorded events (kind + changed field labels). The API has no old → new values, so none are shown.
- Table library removed (`@tanstack/react-table`): the API already sorts and paginates.
- Row-level 1–4 status shortcuts, `g a` / `g t`, « Colonnes », density and pull-to-refresh are not built (§7).

## 10. Validation (Stage A)

Run from `frontend/` unless noted: `npm run lint`, `npm run typecheck`, `npm test` (74 tests),
`npm run build`; `uv run pytest tests/web` (87 passed, API compatibility); `docker compose -f
compose.prod.yaml config -q` (with placeholder secrets). Visual checks used a local synthetic mock API
at 1440×900, 1280×720, 1024×768 and 390×844 in light and dark; an automated axe-core pass
(WCAG 2 A/AA incl. contrast) reported no violation on any screen in either theme. Still to check live:
real queue volumes and column widths, the noVNC reconnect link, and the reading of real OmegaFlow
status values.
