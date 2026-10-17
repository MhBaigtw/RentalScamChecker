import { connection } from "next/server";
import { Suspense } from "react";

import CheckForm from "@/components/CheckForm";
import { DEFAULT_API_URL, fetchHealth } from "@/lib/health";

async function ServiceStatus() {
  // Runs on every request (not prerendered). Called outside any try/catch: with Cache
  // Components, prerender bail-outs are thrown and must not be swallowed.
  await connection();
  const status = await fetchHealth(process.env.API_URL ?? DEFAULT_API_URL);
  if (status.kind === "ok") return null;
  return (
    <p role="status" className="rounded-lg bg-slate-100 px-4 py-3 text-sm text-slate-700">
      The checking service isn&apos;t available right now, so new checks may not go through. Please
      try again in a few minutes.
    </p>
  );
}

const STEPS = [
  ["The price", "against average rents for the city and size"],
  ["The photos", "against photos seen in other listings, including edited copies"],
  ["The text", "for warning signs such as payment before a viewing"],
  ["The address and contact", "whether they check out and appear in other listings"],
] as const;

export default function Home() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 px-4 py-8 sm:py-12">
      <section aria-labelledby="intro-heading" className="flex flex-col gap-4">
        <h1 id="intro-heading" className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Check a rental listing before you pay
        </h1>
        <p className="max-w-2xl text-lg text-slate-700">
          Paste a listing you found and add its photos. We look for risk signals and show you
          the evidence, with a clear next step for each one.
        </p>
        <ul className="grid gap-3 sm:grid-cols-2">
          {STEPS.map(([title, text]) => (
            <li key={title} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
              <span className="font-medium text-slate-900">{title}</span>{" "}
              <span className="text-slate-600">{text}</span>
            </li>
          ))}
        </ul>
      </section>

      <Suspense fallback={null}>
        <ServiceStatus />
      </Suspense>

      <CheckForm />
    </div>
  );
}
