"use client";

import { useState } from "react";
import { Markdown } from "@/components/Markdown";
import { describeError, type Schemas } from "@/lib/api";
import { type KitFile, useKitDownload } from "./kitFiles";
import styles from "./Roles.module.css";

export interface SavedKitProps {
  kit: Schemas["Kit"];
  roleId: string;
}

const FILES: { file: KitFile; label: string }[] = [
  { file: "cover-letter", label: "Download cover letter (PDF)" },
  { file: "cv", label: "Download CV (PDF)" },
];

export function SavedKit({ kit, roleId }: SavedKitProps) {
  const download = useKitDownload();
  const [busy, setBusy] = useState<KitFile | null>(null);
  const [error, setError] = useState<{ title: string; description?: string } | null>(null);
  const get = async (file: KitFile) => {
    setBusy(file);
    setError(null);
    try {
      await download(roleId, file);
    } catch (e) {
      const view = describeError(e, "The download failed");
      setError({
        title: view.title,
        ...(view.description ? { description: view.description } : {}),
      });
    } finally {
      setBusy(null);
    }
  };
  const texts: [string, string | undefined][] = [
    ["CV", kit.cv],
    ["Cover note", kit.cover_note],
    ["Outreach", kit.outreach],
  ];
  return (
    <section className={styles.output} aria-label="Saved kit">
      <p className={styles.muted}>
        Version {kit.version}, generated {kit.generated_at.slice(0, 10)}
      </p>
      <div className={styles.actions}>
        {FILES.filter(({ file }) => file !== "cover-letter" || kit.cover_note).map(
          ({ file, label }) => (
            <button
              key={file}
              type="button"
              className={styles.button}
              disabled={busy !== null}
              aria-busy={busy === file}
              onClick={() => void get(file)}
            >
              {busy === file ? "Preparing…" : label}
              {busy === file ? <span className="visually-hidden">{label}</span> : null}
            </button>
          ),
        )}
      </div>
      {error ? (
        <div role="alert" className={styles.alert}>
          <p>{error.title}</p>
          {error.description ? <p>{error.description}</p> : null}
        </div>
      ) : null}
      {texts.map(([label, text]) =>
        text ? (
          <div key={label}>
            <h3>{label}</h3>
            <Markdown>{text}</Markdown>
          </div>
        ) : null,
      )}
      {kit.answers?.length ? (
        <div>
          <h3>Answers</h3>
          <dl>
            {kit.answers.map((a) => (
              <div key={a.q}>
                <dt>{a.q}</dt>
                <dd>
                  <Markdown>{a.a}</Markdown>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </section>
  );
}
