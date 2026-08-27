"use client";

/**
 * Inventory data store. One hook owns the whole tab's state:
 *
 *   load    — parallel fetch of all six collections + the server alert list.
 *             Loading and error are DISTINCT states: a failed load renders a
 *             retry panel, never an empty lab (empty reads as "owns nothing").
 *   mutate  — every write runs through runMutation (lib.ts): optimistic
 *             apply -> API -> refetch-reconcile (server authoritative on
 *             qtyOut/status/audit) or rollback + toast. A 409 additionally
 *             refetches the touched collection so a retry runs against
 *             current state; it is NEVER auto-retried.
 *
 * Writes are per-mutation — there is no debounced whole-DB save.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { toast } from "../components/toaster";
import {
  Alert,
  ApiError,
  EMPTY_DB,
  InvDB,
  InvItem,
  InvPlaxis,
  InvRes,
  InvTx,
  InvUser,
  MyBench,
  Resource,
  conflictToastText,
  invApi,
  runMutation,
} from "./lib";

export type LoadState = "loading" | "ready" | "error" | "disabled";

/** INVENTORY_PERSONAL_VIEW state: enabled iff GET /api/inventory/me exists
 * (404 = flag off, same probe pattern as /status). `me` is the caller's own
 * slice, refetched alongside every mutation reconcile. */
export type PersonalState = { enabled: boolean; me: MyBench | null };

type MutateArgs = {
  /** Optimistic local change; receives the current DB, returns the next. */
  apply: (db: InvDB) => InvDB;
  /** The API write. */
  request: () => Promise<unknown>;
  /** Collections to refetch on success (server-derived fields) and on 409. */
  refetch: Resource[];
  /** Names the record in the conflict toast. */
  label: string;
};

async function fetchAll(): Promise<InvDB> {
  const [items, tx, res, plaxis, users, audit, alerts] = await Promise.all([
    invApi.list<InvItem>("items"),
    invApi.list<InvTx>("tx"),
    invApi.list<InvRes>("res"),
    invApi.list<InvPlaxis>("plaxis"),
    invApi.list<InvUser>("users"),
    invApi.list<InvDB["audit"][number]>("audit"),
    invApi.alerts(),
  ]);
  return {
    items: items.items,
    tx: tx.items,
    res: res.items,
    plaxis: plaxis.items,
    users: users.items,
    audit: audit.items,
    alerts: alerts.alerts,
  };
}

export function useInventory() {
  const [db, setDb] = useState<InvDB>(EMPTY_DB);
  const [state, setState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<string>("");
  const [photosEnabled, setPhotosEnabled] = useState(false);
  const [personal, setPersonal] = useState<PersonalState>({ enabled: false, me: null });
  const dbRef = useRef(db);
  dbRef.current = db;
  const personalRef = useRef(personal);
  personalRef.current = personal;

  const load = useCallback(async () => {
    setState("loading");
    setLoadError("");
    try {
      const probe = await invApi.status().catch((e: ApiError) => {
        // 404 = router absent = flag off. A disabled feature is a state of
        // its own, never an error panel and never a crash.
        if (e.status === 404) return { enabled: false, photos: false };
        throw e;
      });
      if (!probe.enabled) {
        setState("disabled");
        return;
      }
      setPhotosEnabled(Boolean(probe.photos));
      setDb(await fetchAll());
      // Personal view probe: the /me route exists only when
      // INVENTORY_PERSONAL_VIEW is on — a 404 (or any failure) means the
      // tab renders exactly as it does today, never an error.
      try {
        setPersonal({ enabled: true, me: await invApi.me() });
      } catch {
        setPersonal({ enabled: false, me: null });
      }
      setState("ready");
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "The inventory could not be loaded.");
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Refetch specific collections + the always-server-side audit and alerts
   * (+ the personal slice whenever the personal view is enabled — any write
   * can change what is "mine"). */
  const refetchSome = useCallback(async (resources: Resource[]) => {
    const wanted = Array.from(new Set<Resource>([...resources, "audit"]));
    const [alerts, me, ...lists] = await Promise.all([
      invApi.alerts(),
      personalRef.current.enabled ? invApi.me().catch(() => null) : Promise.resolve(null),
      ...wanted.map((r) => invApi.list<never>(r)),
    ]);
    if (me) setPersonal({ enabled: true, me });
    setDb((prev) => {
      const next: InvDB = { ...prev, alerts: (alerts as { alerts: Alert[] }).alerts };
      wanted.forEach((r, i) => {
        (next as unknown as Record<string, unknown[]>)[r] =
          (lists[i] as { items: unknown[] }).items;
      });
      return next;
    });
  }, []);

  const mutate = useCallback(
    ({ apply, request, refetch, label }: MutateArgs) =>
      runMutation<InvDB>({
        getState: () => dbRef.current,
        setState: setDb,
        apply,
        request,
        reconcile: () => refetchSome(refetch),
        onConflict: async (e) => {
          // The record's current state refused the write: the optimistic
          // apply is already rolled back; refresh so a retry is against
          // reality. Do NOT auto-retry — last-write-wins is exactly what
          // these gates exist to prevent. Flag-on the toast carries the
          // server's own detail (seat/reservation conflicts name the holder
          // and window); flag-off it keeps today's generic text.
          await refetchSome(refetch).catch(() => undefined);
          toast(conflictToastText(personalRef.current.enabled, e.message || "", label));
        },
        onError: (e) => {
          // A 403 from the ownership boundary means a stale tab: the row's
          // owner (or the caller's rights) changed server-side. Same recipe
          // as the conflict toast — roll back (already done), refetch so the
          // view shows reality, and surface the server's message.
          if (e.status === 403 && personalRef.current.enabled) {
            void refetchSome(refetch).catch(() => undefined);
          }
          if (e.status !== 409) toast(e.message || "The change could not be saved.");
        },
      }),
    [refetchSome],
  );

  return { db, state, loadError, load, mutate, photosEnabled, personal };
}
