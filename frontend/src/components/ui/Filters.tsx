import { ChevronDown } from "lucide-react";
import { useId, type ReactNode } from "react";

interface ChipProps {
  selected: boolean;
  onSelect: () => void;
  count?: number;
  children: ReactNode;
}

/** Quick view chip: round, mutually exclusive with its siblings. */
export function FilterChip({ selected, onSelect, count, children }: ChipProps) {
  return (
    <button type="button" className="chip" aria-pressed={selected} onClick={onSelect}>
      {children}
      {count !== undefined && <span className="chip-count">{count}</span>}
    </button>
  );
}

interface FacetProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  allLabel?: string;
}

/** Combinable filter: dashed outline until a value is applied, then solid. */
export function FacetSelect({ label, value, onChange, options, allLabel = "Tous" }: FacetProps) {
  const id = useId();
  return (
    <div className={value !== "" ? "facet facet-active" : "facet"}>
      <label htmlFor={id} className="facet-label">
        {label}
      </label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown size={14} aria-hidden="true" />
    </div>
  );
}
