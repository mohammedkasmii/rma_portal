import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { Loader2 } from "lucide-react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "md" | "lg";

/** Class list shared by <Button> and link-styled buttons (`<a className={buttonClass(...)}>`). */
export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", iconOnly = false): string {
  return ["btn", `btn-${variant}`, size === "lg" ? "btn-lg" : "", iconOnly ? "btn-icon" : ""].filter(Boolean).join(" ");
}

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  /** Icon-only buttons must carry an accessible name (aria-label). */
  iconOnly?: boolean;
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  iconOnly = false,
  children,
  className,
  disabled,
  type = "button",
  ref,
  ...rest
}: Props) {
  return (
    <button
      ref={ref}
      type={type}
      className={[buttonClass(variant, size, iconOnly), className].filter(Boolean).join(" ")}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 className="spin" size={16} aria-hidden="true" /> : icon}
      {children}
    </button>
  );
}
