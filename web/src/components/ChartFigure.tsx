import { type ReactNode, useId } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import styles from "./ChartFigure.module.css";

export interface ChartFigureProps {
  title: string;
  status: "pending" | "error" | "success";
  correlationId?: string | undefined;
  onRetry?: () => void;
  /** One sentence a screen reader user gets instead of the picture. */
  summary: string;
  columns: string[];
  rows: (string | number)[][];
  children: ReactNode;
}

/**
 * Every chart: a title, a text summary, the chart itself (hidden from assistive tech, which gets
 * the summary and the table instead), and the same data as a table.
 */
export function ChartFigure({
  title,
  status,
  correlationId,
  onRetry,
  summary,
  columns,
  rows,
  children,
}: ChartFigureProps) {
  const id = useId();
  return (
    <figure className={styles.figure} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className={styles.figureTitle}>
        {title}
      </h2>
      {status === "pending" ? (
        <LoadingState label={`Loading ${title.toLowerCase()}…`} />
      ) : status === "error" ? (
        <ErrorState
          title={`${title} failed to load`}
          correlationId={correlationId}
          {...(onRetry ? { onRetry } : {})}
        />
      ) : (
        <>
          <figcaption className={styles.summary}>{summary}</figcaption>
          {rows.length ? (
            <>
              <div className={styles.chart} aria-hidden="true">
                {children}
              </div>
              <details className={styles.details}>
                <summary>Data table</summary>
                <table className={styles.table}>
                  <caption className="visually-hidden">{title} data</caption>
                  <thead>
                    <tr>
                      {columns.map((c) => (
                        <th key={c} scope="col">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={String(r[0])}>
                        {r.map((cell, i) =>
                          i === 0 ? (
                            <th key={columns[i]} scope="row">
                              {cell}
                            </th>
                          ) : (
                            <td key={columns[i]}>{cell}</td>
                          ),
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </>
          ) : null}
        </>
      )}
    </figure>
  );
}

interface QueryLike {
  status: "pending" | "error" | "success";
  error: unknown;
  refetch: () => unknown;
}

/** ChartFigure props from a query: its status, error reference and retry. */
export function figureState(q: QueryLike) {
  return {
    status: q.status,
    correlationId: isApiError(q.error) ? q.error.correlationId : undefined,
    onRetry: () => void q.refetch(),
  };
}
