import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useDossierSearch } from "../../api/hooks";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";

const MIN_CHARS = 2;

/** True while the user is typing somewhere, so the "/" shortcut must not steal the key. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * Global dossier search: every file, including dossiers no longer in a queue. Distinct from the
 * list filter above each table, which only narrows the rows already on screen.
 */
export function GlobalSearch() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const debounced = useDebouncedValue(query.trim(), 250);
  const search = useDossierSearch(debounced);
  const enabled = debounced.length >= MIN_CHARS;
  const hits = enabled ? (search.data ?? []) : [];

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !isTyping(event.target)) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (dossierId: number) => {
    setOpen(false);
    setQuery("");
    void navigate(`/dossiers/${dossierId}`);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && hits.length) {
      event.preventDefault();
      setActive((current) => (current + 1) % hits.length);
    } else if (event.key === "ArrowUp" && hits.length) {
      event.preventDefault();
      setActive((current) => (current - 1 + hits.length) % hits.length);
    } else if (event.key === "Enter" && hits.length) {
      event.preventDefault();
      go(hits[Math.min(active, hits.length - 1)].dossier_id);
    } else if (event.key === "Escape") {
      setOpen(false);
      if (!open) setQuery("");
    }
  };

  const showPanel = open && query.trim().length >= MIN_CHARS;
  return (
    <div
      className="global-search"
      role="search"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <Search size={15} aria-hidden="true" />
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label="Rechercher un dossier"
        aria-expanded={showPanel}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showPanel && hits.length ? `${listId}-${active}` : undefined}
        placeholder="Rechercher un dossier — n°, assuré, immatriculation"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <kbd aria-hidden="true">/</kbd>
      {showPanel && (
        <div className="search-panel">
          <ul id={listId} role="listbox" aria-label="Résultats de recherche">
            {hits.map((hit, index) => (
              // Keyboard operation lives on the combobox input (arrows + Enter); options are mouse targets.
              // eslint-disable-next-line jsx-a11y/click-events-have-key-events
              <li
                key={hit.dossier_id}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={index === active ? "search-hit search-hit-active" : "search-hit"}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => go(hit.dossier_id)}
              >
                <span className="mono">{hit.dossier_number || `Dossier ${hit.dossier_id}`}</span>
                <span className="search-hit-main">
                  {hit.insured_name}
                  {hit.registration && <span className="mono muted"> · {hit.registration}</span>}
                </span>
                <span className="muted search-hit-files">{hit.workflows.join(", ") || "hors file"}</span>
              </li>
            ))}
          </ul>
          <p className="search-status" role="status">
            {!enabled || search.isFetching
              ? "Recherche…"
              : search.isError
                ? "La recherche a échoué."
                : hits.length === 0
                  ? "Aucun dossier trouvé."
                  : `${hits.length} résultat${hits.length > 1 ? "s" : ""}`}
          </p>
        </div>
      )}
    </div>
  );
}
