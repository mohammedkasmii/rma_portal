import { BellDot, CircleAlert, Eye, EyeOff, Inbox, MessageSquare } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Button } from "../components/ui/Button";
import { TextField } from "../components/ui/Form";

const HIGHLIGHTS = [
  { icon: Inbox, text: "Les dossiers à traiter, dès leur arrivée" },
  { icon: BellDot, text: "Les changements OmegaFlow signalés, non lus en premier" },
  { icon: MessageSquare, text: "Un statut de traitement et des notes partagés avec l’équipe" },
];

export function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      void navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connexion impossible.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-brand" aria-label="Présentation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">PR</span>
          <span className="brand-name">Portail RMA</span>
        </div>
        <div className="login-pitch">
          <p className="login-title">Le suivi des files OmegaFlow de l’agence.</p>
          <ul>
            {HIGHLIGHTS.map(({ icon: Icon, text }) => (
              <li key={text}>
                <Icon size={18} aria-hidden="true" />
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p className="login-legal">Accès réservé au personnel de l’agence.</p>
      </section>

      <main className="login-panel" id="main">
        <form className="login-form" onSubmit={(event) => void submit(event)}>
          <div>
            <h1>Connexion</h1>
            <p className="page-description">Utilisez votre identifiant agence.</p>
          </div>
          <TextField
            label="Identifiant"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <div className="field-with-action">
            <TextField
              label="Mot de passe"
              type={reveal ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="reveal"
              aria-pressed={reveal}
              aria-label={reveal ? "Masquer le mot de passe saisi" : "Afficher le mot de passe saisi"}
              onClick={() => setReveal((value) => !value)}
            >
              {reveal ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
              {reveal ? "Masquer" : "Afficher"}
            </button>
          </div>
          <div role="alert" aria-live="assertive">
            {error && (
              <p className="login-error">
                <CircleAlert size={16} aria-hidden="true" /> {error}
              </p>
            )}
          </div>
          <Button type="submit" variant="primary" size="lg" loading={busy}>
            {busy ? "Connexion…" : "Se connecter"}
          </Button>
          <p className="field-hint">Mot de passe oublié ? Contactez un administrateur du portail.</p>
        </form>
      </main>
    </div>
  );
}
