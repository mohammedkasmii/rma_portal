# Captured OmegaFlow read contract

Evidence source: the local `RMA_FIRST` ScriptScrap capture
(`script_scrap/rma_data/rma_data/RMA_FIRST`, read-only, never copied into
this repository). This document records the *structure* confirmed from that
capture — selectors, DOM shape, URL patterns — never any real customer
data, dossier number, name, registration or amount.

## Route and list contract used by V1

- Start route: `https://omegaflow.ma/#dossiers-en-instance-accord/` (no slash after `#` — confirmed from the RMA_FIRST capture; the `#/` form is not a valid OmegaFlow route)
- List root: `#view_1874`
- Procedure filter: `#kn-conn-1-field_219`. The captured markup shows this
  native `<select>` rendered `style="display: none"` behind OmegaFlow's
  Chosen widget (`class="chzn-select chzn-done"`) — it is never actually
  visible. V1 selects it with Playwright's `force=True` (bypassing the
  visibility actionability check that would otherwise wait indefinitely),
  then reads the value back via `input_value()` to confirm the selection
  stuck, and explicitly dispatches `input`/`change` events. Confirm the
  event-driven refresh against a live session.
  - Required option label: `Garage agréé`
  - Captured option value: `5ed644a2faf17c0015d8c367`
- The filter panel must be **expanded** before `#kn-submit-filters` exists
  in the DOM at all (it is not present by default). The capture did not
  record the exact toggle selector for "ajouter des filtres" — V1's reader
  falls back to locating that French label by text, scoped to `#view_1874`
  (preferring a `.kn-add-filter` element when present) so it can never
  interact with an unrelated element elsewhere on the page. **Requires a
  live session to confirm the exact toggle markup.**
- Search/submit button (only after the panel above is expanded):
  `#kn-submit-filters`
- A successfully filtered view with **zero** matching dossiers is a valid,
  `COMPLETE` result — V1 does not wait for a row to exist, only for the
  view's AJAX refresh to settle (network-idle plus, if present, a loading
  indicator clearing). The loading indicator's exact markup was not
  captured. **Requires a live session to confirm.**
- Rows: `#view_1874 table tbody tr[id]`
  - The `id` attribute is the stable portal identity (Knack record id: 24
    lowercase hex characters observed in the capture).
- Details link: an `<a href>` containing `view-dossier-details/` (with the
  trailing slash). Some rows also carry a **decoy** link to an
  "observations" icon whose href contains `view-dossier-details7/` instead
  — the trailing-slash requirement is what tells the two apart; do not
  match on `view-dossier-details` alone.
- Pagination: `#view_1874 .kn-page-select` (a `<select>` with one
  `<option value="N">` per page — the highest value is the page count) and
  `.kn-change-page.kn-next` (an enabled "next" link; a disabled one gets a
  ` disabled` class appended, there is no separate disabled attribute). V1
  navigates by setting `.kn-page-select`'s value directly rather than
  repeatedly clicking "next", since the total page count is already known
  from the same markup.

## List field mapping

Every field below is a class present somewhere inside the row (V1's parser
selects `.field_N` without assuming a specific tag, since the exact
wrapping element/attributes are Knack rendering detail):

| Field class | Meaning |
|---|---|
| `field_1` | dossier number |
| `field_3` | insured/name |
| `field_219` | procedure |
| `field_8` | registration |
| `field_40` | garage |
| `field_100` | estimate amount (raw text, currency/formatting kept as-is) |
| `field_77` | portal status |
| `field_655` | city |
| `field_318` | observation count — only seen as a `<th>` header in the capture, its `<td>` body shape in a populated row was not confirmed. **Requires a live session to confirm**; the parser reads it as plain text like every other field and never assumes it is numeric. |
| `field_300` | agreement login |

## Detail field mapping

Each of the five dates is rendered as `.field_N .kn-detail-body` (confirmed
shape: a `kn-detail` container with a `kn-detail-label` and a
`kn-detail-body`, itself wrapping the value in nested `<span>`s — V1 reads
the body's full text content regardless of that nesting):

| Field class | Meaning |
|---|---|
| `field_107` | Date création |
| `field_113` | Première date de fin prévue |
| `field_138` | Date fin de travaux prévue |
| `field_114` | **Date envoi devis garage** — shown prominently in the dashboard and dossier page |
| `field_260` | Date Photos Avant |

Dates are formatted `dd/mm/yyyy` or `dd/mm/yyyy HH:MM[:SS]` and parsed in
the `Africa/Casablanca` timezone. Empty, dash (`-`/`—`) or unparseable text
always leaves the parsed value `None` while keeping the raw text verbatim
(`domain.models.DossierDates.*_raw`) — nothing is ever silently dropped.

## Authentication / session states

Two distinct unauthenticated states were confirmed in the capture, and both
must be treated as `AUTH_REQUIRED`:

1. **Login form** — a `.kn-login-form` with an email field and a
   `type="password"` input.
2. **Session revalidation panel** — a `.kn-login-form.panel` with the
   heading "Validate your session" and a `#refreshSession` button, but *no*
   credential fields. `Configurer_Session_RMA.bat` never automates this
   button; an administrator must resolve it manually in the visible
   browser.

## Future direct-API candidates (documented only, not called by V1)

The capture observed these GET endpoints on `*.knack.com`, but request
bodies for `*.knack.com` were out of scope for the capture (only
`omegaflow.ma` response bodies were retained), so their authenticated wire
contract — headers, exact response schema, pagination/query parameters — is
not established:

- `GET /v1/scenes/scene_1059/views/view_1874/records`
- `GET /v1/scenes/scene_20/views/view_23/records/{record_id}`
- `GET /v1/scenes/scene_1059/views/view_1874/connections/field_219`
- `GET /v1/scenes/scene_1059/views/view_1874/connections/field_40`
- `GET /v1/scenes/scene_1059/views/view_1874/connections/field_655`

V1 must not call these directly or guess their response schema or
authentication requirements. A future direct-API `PortalReader`
implementation is possible without changing `SyncAgreementQueue` or the web
layer (see `docs/architecture.md`), but must first capture and verify this
contract properly.
