/**
 * Convert backend ISO timestamps (always UTC, with ``+00:00`` suffix) to
 * the operator's local-tz display.
 *
 * Background: every timestamp the API emits — ``decisions.created_at``,
 * ``active_tasks.json::started_at``, ``run_log.started_at`` /
 * ``finished_at``, the schedule's ``last_*_at`` fields — is stored as
 * UTC, written as a tz-aware ISO string (post-2026-05-27). Legacy entries
 * without an offset suffix are also treated as UTC; ``new Date()``
 * happens to do the right thing as long as the offset suffix exists, so
 * we normalise here.
 *
 * Use ``formatLocal(iso)`` for display in cards / lists.
 * Use ``formatLocalDateOnly(iso)`` when only the date part is shown.
 */
export function useFormatTime() {
  function toDate(iso: string | null | undefined): Date | null {
    if (!iso) return null;
    // Legacy: pre-2026-05-27 strings like "2026-05-20T01:23:45" with no
    // offset. JavaScript's Date treats those as local time, which is
    // wrong (they're really UTC). Append a Z so parsing is unambiguous.
    const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
    const d = new Date(normalised);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  /**
   * Locale-friendly local-tz timestamp, e.g. ``2026-05-27 09:23:45``.
   * Uses the ``sv-SE`` locale because it formats as ISO-shaped
   * ``YYYY-MM-DD HH:mm:ss`` regardless of the operator's actual locale.
   */
  function formatLocal(iso: string | null | undefined): string {
    const d = toDate(iso);
    if (!d) return "";
    return d.toLocaleString("sv-SE", { hour12: false });
  }

  /** Local-tz date only, e.g. ``2026-05-27``. */
  function formatLocalDateOnly(iso: string | null | undefined): string {
    const d = toDate(iso);
    if (!d) return "";
    return d.toLocaleDateString("sv-SE");
  }

  return { toDate, formatLocal, formatLocalDateOnly };
}
