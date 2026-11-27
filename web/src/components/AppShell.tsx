import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./AppShell.module.css";
import { SkipLink } from "./SkipLink";

export interface NavItem {
  href: string;
  label: string;
}

export interface AppShellProps {
  navItems: readonly NavItem[];
  currentPath: string;
  /** Shown next to the sign-out control: the signed-in account's email. */
  accountLabel?: string;
  /** A short note after the account label, e.g. the role. */
  accountDetail?: string;
  /** Extra controls on the right of the top bar, before the account (e.g. status and Health). */
  actions?: ReactNode;
  onSignOut: () => void;
  children: ReactNode;
}

const MAIN_ID = "main";

function isCurrent(currentPath: string, href: string): boolean {
  return currentPath === href || currentPath.startsWith(`${href}/`);
}

/** The signed-in frame: a top bar (brand, main navigation, actions, account) and the main area. */
export function AppShell({
  navItems,
  currentPath,
  accountLabel,
  accountDetail,
  actions,
  onSignOut,
  children,
}: AppShellProps) {
  return (
    <div className={styles.shell}>
      <SkipLink targetId={MAIN_ID} />
      <header className={styles.header}>
        <div className={styles.bar}>
          <Link href="/dashboard" className={styles.brand}>
            <RadarMark />
            Job Radar
          </Link>
          <nav className={styles.nav} aria-label="Main">
            <ul className={styles.navList}>
              {navItems.map((item) => {
                const current = isCurrent(currentPath, item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={
                        current ? `${styles.navLink} ${styles.navLinkCurrent}` : styles.navLink
                      }
                      aria-current={current ? "page" : undefined}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className={styles.account}>
            {actions}
            {accountLabel ? (
              <span className={styles.accountLabel}>
                <span className={styles.accountEmail} title={accountLabel}>
                  {accountLabel}
                </span>
                {accountDetail ? <span> · {accountDetail}</span> : null}
              </span>
            ) : null}
            <button type="button" className={styles.signOut} onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        {children}
      </main>
    </div>
  );
}

/** The brand mark: radar rings with a sweep and a blip. Decorative; the link text names it. */
function RadarMark() {
  return (
    <svg
      className={styles.brandMark}
      viewBox="0 0 24 24"
      width="24"
      height="24"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle
        cx="12"
        cy="12"
        r="6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        opacity="0.6"
      />
      <circle
        cx="12"
        cy="12"
        r="2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        opacity="0.6"
      />
      <path d="M12 12 L12 2 A10 10 0 0 1 20.66 7 Z" fill="currentColor" opacity="0.22" />
      <path d="M12 12 L20.66 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="16.5" cy="5.6" r="1.4" fill="var(--color-hot)" />
    </svg>
  );
}
