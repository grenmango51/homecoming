import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useServerFn } from "@tanstack/react-start";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";
import { lovable } from "@/integrations/lovable/index";
import flightCss from "@/flight/flight.css?url";
import { FlightApp } from "@/flight/FlightApp";
import { getFlightSession } from "@/lib/flight.functions";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Flight Finder — Private flight search" },
      { name: "description", content: "Private Google Flights and Skyscanner price search with flexible dates." },
      { property: "og:title", content: "Flight Finder — Private flight search" },
      { property: "og:description", content: "Private Google Flights and Skyscanner price search with flexible dates." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "robots", content: "noindex" },
      { name: "theme-color", content: "#071d49" },
    ],
    links: [{ rel: "stylesheet", href: flightCss }],
  }),
  component: Index,
});

type Phase = "loading" | "signed-out" | "not-owner" | "owner";

function Index() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [phase, setPhase] = useState<Phase>("loading");
  const loadSession = useServerFn(getFlightSession);

  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (session === undefined) return;
    if (!session) { setPhase("signed-out"); return; }
    loadSession().then((r) => setPhase(r.owner ? "owner" : "not-owner")).catch(() => setPhase("not-owner"));
  }, [session === undefined ? "pending" : (session?.user.id ?? "none")]); // eslint-disable-line react-hooks/exhaustive-deps

  if (phase === "owner") return <FlightApp />;
  return <Gate phase={phase} email={session?.user.email} />;
}

function Header() {
  return (
    <header className="site-header">
      <div className="shell nav-shell">
        <span className="brand"><span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" fill="none"><path d="M5 9.5h20.5M8.5 15.5h15M12 21.5h9" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" /><path d="M6 24.5 26 7" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" /></svg>
        </span><span>Flight<span>Finder</span></span></span>
        <div className="nav-meta"><span className="archive-dot" aria-hidden="true" />Private flight search</div>
      </div>
    </header>
  );
}

function Gate({ phase, email }: { phase: Phase; email?: string | undefined }) {
  const [error, setError] = useState("");
  const signIn = async (provider: "google" | "lovable") => {
    setError("");
    const r = await lovable.auth.signInWithOAuth(provider, { redirect_uri: window.location.origin });
    if (r.error) setError("Sign-in did not complete. Please try again.");
  };
  return (
    <>
      <Header />
      <main>
        <section className="shell search-wrap">
          <div className="search-card" style={{ maxWidth: 480, margin: "0 auto" }}>
            {phase === "loading" && <p className="form-disclaimer">Loading…</p>}
            {phase === "signed-out" && (
              <>
                <p className="eyebrow">Private</p>
                <p className="summary-title" style={{ fontSize: "1.2rem", fontWeight: 850, color: "var(--navy)" }}>Sign in to search flights</p>
                <p className="form-disclaimer" style={{ margin: "8px 0 20px" }}>This Flight Finder is private to its owner.</p>
                <div style={{ display: "grid", gap: 10 }}>
                  <button className="search-button" type="button" onClick={() => signIn("lovable")}><span>Continue with Lovable</span></button>
                  <button className="text-button" type="button" onClick={() => signIn("google")}>Continue with Google</button>
                </div>
              </>
            )}
            {phase === "not-owner" && (
              <>
                <p className="summary-title" style={{ fontSize: "1.1rem", fontWeight: 850, color: "var(--navy)" }}>This Flight Finder is private</p>
                <p className="form-disclaimer" style={{ margin: "8px 0 20px" }}>{email ? `${email} doesn't have access.` : "Your account doesn't have access."}</p>
                <button className="text-button" type="button" onClick={() => supabase.auth.signOut()}>Sign out</button>
              </>
            )}
            {error && <p className="form-message" role="alert">{error}</p>}
          </div>
        </section>
      </main>
    </>
  );
}

