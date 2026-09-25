import { CircleAlert, CircleCheck } from "lucide-react";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export interface ToastInput {
  message: string;
  tone?: "success" | "error";
  action?: { label: string; onSelect: () => void };
}
interface ToastItem extends ToastInput {
  id: number;
}

type Show = (toast: ToastInput) => void;
const ToastContext = createContext<Show | null>(null);
const DURATION_MS = 5000;

/** Toasts live 5 s in a polite live region and may carry one action (e.g. "Annuler"). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: number) => setToasts((current) => current.filter((toast) => toast.id !== id)), []);
  const show = useCallback<Show>(
    (toast) => {
      const id = ++counter.current;
      setToasts((current) => [...current.slice(-2), { ...toast, id }]);
      window.setTimeout(() => dismiss(id), DURATION_MS);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.tone ?? "success"}`}>
            {toast.tone === "error" ? (
              <CircleAlert size={16} aria-hidden="true" />
            ) : (
              <CircleCheck size={16} aria-hidden="true" />
            )}
            <span>{toast.message}</span>
            {toast.action && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  toast.action?.onSelect();
                  dismiss(toast.id);
                }}
              >
                {toast.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const noop: Show = () => undefined;

/** Outside a provider (isolated component tests) toasts are silently dropped. */
export function useToast(): Show {
  return useContext(ToastContext) ?? noop;
}
