import { useState, type FormEvent, type KeyboardEvent, type Ref } from "react";
import { useAddNote } from "../api/hooks";
import type { MembershipView, NoteView } from "../api/types";
import { formatWhen } from "../labels";
import { Button } from "./ui/Button";
import { useToast } from "./ui/Toast";

interface Props {
  dossierId: number;
  notes: NoteView[];
  memberships: MembershipView[];
  /** Lets the page focus the field (e.g. from the mobile action bar). */
  textareaRef?: Ref<HTMLTextAreaElement>;
}

const draftKey = (dossierId: number) => `rma-note-draft-${dossierId}`;

function readDraft(dossierId: number): string {
  try {
    return localStorage.getItem(draftKey(dossierId)) ?? "";
  } catch {
    return "";
  }
}

/** Shared notes of the dossier, newest first. The unsent draft survives leaving the page. */
export function NotesPanel({ dossierId, notes, memberships, textareaRef }: Props) {
  const addNote = useAddNote(dossierId);
  const toast = useToast();
  const [note, setNote] = useState(() => readDraft(dossierId));
  const [context, setContext] = useState("");

  const remember = (value: string) => {
    setNote(value);
    try {
      if (value) localStorage.setItem(draftKey(dossierId), value);
      else localStorage.removeItem(draftKey(dossierId));
    } catch {
      /* the draft is a convenience only */
    }
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!note.trim() || addNote.isPending) return;
    addNote.mutate(
      { body: note, membershipId: context ? Number(context) : null },
      {
        onSuccess: () => {
          remember("");
          toast({ message: "Note ajoutée." });
        },
        onError: () => toast({ tone: "error", message: "La note n’a pas pu être ajoutée." }),
      },
    );
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  };

  const ordered = [...notes].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <section className="card notes-panel" aria-labelledby="notes-title">
      <div className="card-head">
        <h2 id="notes-title">
          Notes <span className="page-count">{notes.length}</span>
        </h2>
        <span className="meta">Visibles par toute l’équipe</span>
      </div>
      <form onSubmit={submit} className="notes-form">
        <div className="field">
          <label htmlFor="note-body">Nouvelle note</label>
          <textarea
            id="note-body"
            ref={textareaRef}
            className="input"
            rows={3}
            maxLength={2000}
            required
            value={note}
            onChange={(event) => remember(event.target.value)}
            onKeyDown={onKeyDown}
            aria-describedby="note-hint"
          />
          <p id="note-hint" className="field-hint">Ctrl + Entrée pour publier. Le brouillon est conservé si vous quittez la page.</p>
        </div>
        <div className="field">
          <label htmlFor="note-context">Contexte (facultatif)</label>
          <select id="note-context" className="input" value={context} onChange={(event) => setContext(event.target.value)}>
            <option value="">Dossier entier</option>
            {memberships.map((m) => (
              <option key={m.membership_id} value={m.membership_id}>
                {m.workflow_name}
              </option>
            ))}
          </select>
        </div>
        <div className="notes-submit">
          <Button type="submit" variant="primary" loading={addNote.isPending} disabled={!note.trim()}>
            Ajouter la note
          </Button>
          <span role="status" aria-live="polite" className="error-text">
            {addNote.isError && addNote.error.message}
          </span>
        </div>
      </form>
      {ordered.length === 0 ? (
        <p className="list-empty">Aucune note pour ce dossier.</p>
      ) : (
        <ul className="notes">
          {ordered.map((n) => (
            <li key={n.id} className="note">
              <p className="note-meta">
                <strong>{n.author}</strong>
                <span className="muted">
                  {" "}
                  · <time dateTime={n.created_at}>{formatWhen(n.created_at)}</time>
                  {n.workflow_name && ` · ${n.workflow_name}`}
                </span>
              </p>
              <p className="note-body">{n.body}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
