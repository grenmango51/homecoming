import { memo, useEffect, useRef, useState } from "react";
import { useServerFn } from "@tanstack/react-start";
import { supabase } from "@/integrations/supabase/client";
import { FLIGHT_MARKUP } from "@/flight/markup";
import { cancelFlightJob, getFlightJob, getFlightSession, submitFlightSearch } from "@/lib/flight.functions";

export const FlightApp = memo(function FlightApp() {
  const root = useRef<HTMLDivElement>(null);
  const [mountError, setMountError] = useState("");
  const session = useServerFn(getFlightSession);
  const submit = useServerFn(submitFlightSearch);
  const get = useServerFn(getFlightJob);
  const cancel = useServerFn(cancelFlightJob);
  const api = useRef({ session, submit, get, cancel });
  api.current = { session, submit, get, cancel };

  useEffect(() => {
    const host = root.current;
    if (!host) return;
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    // Trusted static markup is written once, imperatively; React never re-renders inside this node.
    host.innerHTML = FLIGHT_MARKUP;
    import("@/flight/legacy-app.js").then(({ mountFlightFinder }) => {
      if (cancelled) return;
      cleanup = mountFlightFinder({
        ready: () => true,
        session: () => api.current.session(),
        submit: (search: unknown) => api.current.submit({ data: { search, idempotency_key: crypto.randomUUID() } }),
        get: (id: string) => api.current.get({ data: { id } }),
        cancel: (id: string) => api.current.cancel({ data: { id } }),
        signOut: async () => { await supabase.auth.signOut(); window.location.replace("/"); },
      });
    }).catch(() => { if (!cancelled) setMountError("The search page could not load. Please refresh."); });
    return () => { cancelled = true; cleanup?.(); host.innerHTML = ""; };
  }, []);

  return (
    <>
      {mountError && <p className="form-message" role="alert">{mountError}</p>}
      <div ref={root} />
    </>
  );
});

