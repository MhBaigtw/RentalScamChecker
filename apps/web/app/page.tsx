import { connection } from "next/server";
import { Suspense } from "react";

import { DEFAULT_API_URL, fetchHealth, type HealthStatus } from "@/lib/health";

const LABELS: Record<HealthStatus["kind"], { text: string; className: string }> = {
  ok: { text: "All systems working", className: "bg-green-100 text-green-900" },
  degraded: { text: "API is up but reporting a problem", className: "bg-amber-100 text-amber-900" },
  unreachable: { text: "API is not reachable", className: "bg-red-100 text-red-900" },
};

async function ApiHealth() {
  // Stop prerendering here so the check runs on every request. Called outside any try/catch:
  // with Cache Components, prerender bail-outs are thrown and must not be swallowed.
  await connection();
  const status = await fetchHealth(process.env.API_URL ?? DEFAULT_API_URL);
  const label = LABELS[status.kind];

  return (
    <div className={`rounded-md px-4 py-3 ${label.className}`} role="status">
      <p className="font-medium">{label.text}</p>
      {status.kind === "degraded" && (
        <p className="mt-1 text-sm">
          {status.message} (HTTP {status.httpStatus}, {status.code})
        </p>
      )}
      {status.kind === "unreachable" && <p className="mt-1 text-sm">{status.reason}</p>}
    </div>
  );
}

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-4 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">RentalScamChecker</h1>
        <p className="text-zinc-700 dark:text-zinc-300">
          Check a rental listing for risk signals before you send any money.
        </p>
      </header>

      <section aria-labelledby="status-heading" className="flex flex-col gap-3">
        <h2 id="status-heading" className="text-lg font-medium">
          Service status
        </h2>
        <Suspense fallback={<p className="text-zinc-600">Checking the API…</p>}>
          <ApiHealth />
        </Suspense>
      </section>
    </main>
  );
}
