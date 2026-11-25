import type { ReactNode } from "react";

/** Shown in place of content when there is nothing to show yet: a title and one or two lines. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <section className="empty-state">
      <h2>{title}</h2>
      {children && <p>{children}</p>}
    </section>
  );
}
