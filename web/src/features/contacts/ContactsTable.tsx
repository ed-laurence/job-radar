import type { Contact } from "./api";
import styles from "./Contacts.module.css";
import { CLOSENESS_LABELS, fullName, STATUS_LABELS } from "./labels";

export interface ContactsTableProps {
  contacts: Contact[];
  onEdit: (contact: Contact) => void;
  onDelete: (contact: Contact) => void;
}

export function ContactsTable({ contacts, onEdit, onDelete }: ContactsTableProps) {
  return (
    <table className={styles.table}>
      <caption className="visually-hidden">Contacts</caption>
      <thead>
        <tr>
          <th scope="col">Name</th>
          <th scope="col">Company</th>
          <th scope="col">Position</th>
          <th scope="col">Status</th>
          <th scope="col">Closeness</th>
          <th scope="col">Email</th>
          <th scope="col">
            <span className="visually-hidden">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {contacts.map((c) => {
          const name = fullName(c);
          return (
            <tr key={c.id}>
              <th scope="row">{name}</th>
              <td>{c.company ?? "—"}</td>
              <td>{c.position ?? "—"}</td>
              <td>{STATUS_LABELS[c.status]}</td>
              <td>{c.closeness ? CLOSENESS_LABELS[c.closeness] : "—"}</td>
              <td>{c.email ?? "—"}</td>
              <td>
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.button}
                    aria-label={`Edit ${name}`}
                    onClick={() => onEdit(c)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className={styles.danger}
                    aria-label={`Delete ${name}`}
                    onClick={() => onDelete(c)}
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
