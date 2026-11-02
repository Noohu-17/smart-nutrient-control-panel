import { Link } from "react-router-dom";
import { EmptyState } from "./EmptyState";

export function NotFound() {
  return (
    <EmptyState title="Page not found">
      This page does not exist. <Link to="/">Back to the Overview</Link>
    </EmptyState>
  );
}
