import Link from "next/link";
import { ageLabel } from "./age";
import { formatDate, type Role, STATUS_LABELS, WORK_MODE_LABELS } from "./labels";
import styles from "./Roles.module.css";

export interface RolesTableProps {
  roles: Role[];
}

export function RolesTable({ roles }: RolesTableProps) {
  return (
    <table className={styles.table}>
      <caption className="visually-hidden">Roles</caption>
      <thead>
        <tr>
          <th scope="col">Title</th>
          <th scope="col">Company</th>
          <th scope="col">Status</th>
          <th scope="col">Score</th>
          <th scope="col">Work mode</th>
          <th scope="col">Found</th>
          <th scope="col">Age</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((role) => (
          <tr key={role.id}>
            <td>
              <Link href={`/roles/${role.id}`}>{role.title}</Link>
            </td>
            <td>{role.company ?? "—"}</td>
            <td>{STATUS_LABELS[role.status]}</td>
            <td>{role.score ?? "—"}</td>
            <td>{role.work_mode ? WORK_MODE_LABELS[role.work_mode] : "—"}</td>
            <td>{formatDate(role.found_at)}</td>
            <td>{ageLabel(role)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
