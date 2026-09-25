import type { ReactNode } from "react";

interface Props {
  title: string;
  count?: number;
  description?: ReactNode;
  breadcrumb?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, count, description, breadcrumb, actions }: Props) {
  return (
    <header className="page-header">
      {breadcrumb && <div className="breadcrumb">{breadcrumb}</div>}
      <div className="page-header-row">
        <div>
          <h1>
            {title}
            {count !== undefined && <span className="page-count"> {count}</span>}
          </h1>
          {description && <p className="page-description">{description}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </header>
  );
}
