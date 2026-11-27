import type { Target } from "./api";
import { LANE_LABELS, ORIGIN_LABELS, STATUS_LABELS } from "./labels";
import styles from "./Targets.module.css";

export interface TargetsTableProps {
  targets: Target[];
  onEdit: (target: Target) => void;
  onResearch: (target: Target) => void;
  onDelete: (target: Target) => void;
}

export function TargetsTable({ targets, onEdit, onResearch, onDelete }: TargetsTableProps) {
  return (
    <table className={styles.table}>
      <caption className="visually-hidden">Employers</caption>
      <thead>
        <tr>
          <th scope="col">Name</th>
          <th scope="col">Lane</th>
          <th scope="col">Status</th>
          <th scope="col">Origin</th>
          <th scope="col">Why</th>
          <th scope="col">
            <span className="visually-hidden">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {targets.map((t) => (
          <tr key={t.id}>
            <th scope="row">{t.name}</th>
            <td>{LANE_LABELS[t.lane]}</td>
            <td>{STATUS_LABELS[t.status]}</td>
            <td>{ORIGIN_LABELS[t.origin]}</td>
            <td className={styles.why}>{t.why ?? "—"}</td>
            <td>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.button}
                  aria-label={`Edit ${t.name}`}
                  onClick={() => onEdit(t)}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className={styles.button}
                  aria-label={`Research ${t.name}`}
                  onClick={() => onResearch(t)}
                >
                  Research
                </button>
                <button
                  type="button"
                  className={styles.danger}
                  aria-label={`Delete ${t.name}`}
                  onClick={() => onDelete(t)}
                >
                  Delete
                </button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
