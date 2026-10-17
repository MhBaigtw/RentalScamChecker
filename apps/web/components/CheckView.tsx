"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchCheck, type Check } from "@/lib/api";

import ProgressView from "./ProgressView";
import Report from "./Report";

type State =
  | { kind: "loading" }
  | { kind: "ok"; check: Check }
  | { kind: "not_found" }
  | { kind: "failed"; message: string };

// Poll every second while signals run; slow down after the first half minute.
function pollDelay(elapsedMs: number): number {
  return elapsedMs < 30_000 ? 1000 : 3000;
}

export default function CheckView({ id }: { id: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();

    async function poll() {
      const result = await fetchCheck(id);
      if (cancelled) return;
      if (result.kind === "ok") {
        setState({ kind: "ok", check: result.check });
        if (result.check.status !== "complete") {
          timer = setTimeout(poll, pollDelay(Date.now() - started));
        }
      } else if (result.kind === "not_found") {
        setState({ kind: "not_found" });
      } else {
        setState({ kind: "failed", message: result.message });
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, attempt]);

  if (state.kind === "loading") {
    return <p className="text-slate-600">Loading your check…</p>;
  }
  if (state.kind === "not_found") {
    return (
      <div className="flex flex-col gap-3">
        <h1 className="text-2xl font-semibold">We couldn&apos;t find this check</h1>
        <p className="text-slate-700">The link may be incomplete. Check the address in your browser.</p>
        <Link href="/" className="font-medium text-teal-800 underline">
          Start a new check
        </Link>
      </div>
    );
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="flex flex-col items-start gap-3">
        <h1 className="text-2xl font-semibold">We couldn&apos;t load your check</h1>
        <p className="text-slate-700">{state.message} Your check is saved; try again in a moment.</p>
        <button
          type="button"
          onClick={() => {
            setState({ kind: "loading" });
            setAttempt((n) => n + 1);
          }}
          className="rounded-lg bg-teal-700 px-4 py-2 font-semibold text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        >
          Try again
        </button>
      </div>
    );
  }
  const { check } = state;
  return check.status === "complete" && check.risk ? (
    <Report check={check} />
  ) : (
    <ProgressView check={check} />
  );
}
