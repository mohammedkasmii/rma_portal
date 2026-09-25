import { useState } from "react";
import { Link } from "react-router-dom";
import { useDossierSearch } from "../api/hooks";
import { ItemsBrowser } from "../components/ItemsBrowser";

function GlobalSearch() {
  const [q, setQ] = useState("");
  const hits = useDossierSearch(q);
  return (
    <section className="card" aria-labelledby="find">
      <h2 id="find">Retrouver un dossier (tous workflows, y compris hors file)</h2>
      <div className="field">
        <label htmlFor="global-search">N° dossier, assuré, immatriculation ou garage (2 caractères min.)</label>
        <input id="global-search" type="search" value={q} onChange={(event) => setQ(event.target.value)} />
      </div>
      <div aria-live="polite">
        {hits.data && hits.data.length === 0 && <p>Aucun dossier trouvé.</p>}
        {hits.data && hits.data.length > 0 && (
          <ul className="hits">
            {hits.data.map((hit) => (
              <li key={hit.dossier_id}>
                <Link to={`/dossiers/${hit.dossier_id}`}>{hit.dossier_number || `Dossier ${hit.dossier_id}`}</Link> —{" "}
                {hit.insured_name} · {hit.registration} · {hit.workflows.join(", ") || "hors file"}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export function Inbox() {
  return (
    <>
      <h1>Boîte de travail</h1>
      <GlobalSearch />
      <ItemsBrowser workflowKey={null} caption="Dossiers actifs de tous les workflows" />
    </>
  );
}
