import Link from "next/link";

import type { Check, SignalResult } from "@/lib/api";
import {
  LEVEL_STYLES,
  TONE_STYLES,
  basedOnOtherListings,
  evidenceRows,
  finding,
  signalInfo,
  type EvidenceRow,
} from "@/lib/signals";

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2, none: 3 } as const;

// Signals that found something first, then the rest; not-checked and failed ones last.
function ordered(signals: SignalResult[]): SignalResult[] {
  const rank = (s: SignalResult) =>
    s.status === "ok" ? SEVERITY_ORDER[s.severity ?? "none"] : s.status === "error" ? 4 : 5;
  return [...signals].sort((a, b) => rank(a) - rank(b));
}

const SAFE_STEPS = [
  "See the place in person, or on a live video call that you start, before paying anything.",
  "Never pay a deposit or rent before you have viewed the unit and signed a written lease.",
  "Confirm the person owns or manages the unit, for example with ID matching a property record.",
  "Pay in a way you can trace, and get a receipt. Avoid gift cards, crypto and wire transfers.",
];

function Evidence({ rows }: { rows: EvidenceRow[] }) {
  return (
    <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]">
      {rows.map((row, i) => (
        <div key={`${row.label}-${i}`} className="contents">
          <dt className="font-medium text-slate-700">{row.label}</dt>
          <dd className="min-w-0 break-words text-slate-900">
            {typeof row.value === "string" ? (
              row.value
            ) : (
              <ol className="flex flex-col gap-2">
                {row.value.map((item, j) => (
                  <li key={j} className="rounded-md bg-slate-50 p-2 ring-1 ring-slate-200">
                    <Evidence rows={item} />
                  </li>
                ))}
              </ol>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SignalCard({ result }: { result: SignalResult }) {
  const info = signalInfo(result.name);
  const f = finding(result);
  const headingId = `signal-${result.name}`;
  return (
    <article
      aria-labelledby={headingId}
      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 id={headingId} className="text-lg font-semibold text-slate-900">
          {info.title}
        </h3>
        <span className={`rounded-full px-2.5 py-1 text-sm font-medium ring-1 ${TONE_STYLES[f.tone]}`}>
          {f.label}
        </span>
      </div>
      {info.checks && (
        <p className="mt-1 text-sm text-slate-600">
          <span className="font-medium text-slate-700">What we checked: </span>
          {info.checks}
        </p>
      )}
      {result.explanation && (
        <p className="mt-3 text-slate-800">
          <span className="sr-only">What we found: </span>
          {result.explanation}
        </p>
      )}
      {basedOnOtherListings(result) && result.severity !== "none" && (
        <p className="mt-2 text-sm text-slate-600">
          This result comes from other listings linked to this one, so on its own it can raise the
          overall level by one step at most.
        </p>
      )}
      {result.next_step && (
        <div className="mt-3 rounded-lg bg-teal-50 px-3 py-2 text-teal-950">
          <span className="font-semibold">Next step: </span>
          {result.next_step}
        </div>
      )}
      {Object.keys(result.evidence).length > 0 && (
        <details className="group mt-3">
          <summary className="cursor-pointer rounded text-sm font-medium text-teal-800 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-teal-700">
            Show the evidence
          </summary>
          <div className="mt-3">
            <Evidence rows={evidenceRows(result.evidence, result.name)} />
          </div>
        </details>
      )}
    </article>
  );
}

function dollars(cents: number): string {
  return `$${(cents / 100).toLocaleString("en-CA")}`;
}

export default function Report({ check }: { check: Check }) {
  const risk = check.risk!;
  const style = LEVEL_STYLES[risk.level];
  const contributing = risk.contributing.map((n) => signalInfo(n).title);
  const missing = risk.missing.map((n) => signalInfo(n).title);
  const related = check.related;
  const { listing } = check;

  return (
    <div className="flex flex-col gap-8">
      <section aria-labelledby="level-heading" className={`rounded-xl border p-5 sm:p-6 ${style.panel}`}>
        <p className="text-sm font-medium uppercase tracking-wide opacity-80">Overall</p>
        <h1 id="level-heading" className="mt-1 flex items-center gap-3 text-2xl font-semibold sm:text-3xl">
          <span aria-hidden="true" className={`h-3.5 w-3.5 rounded-full ${style.dot}`} />
          {risk.label}
        </h1>
        <p className="mt-3 text-base leading-relaxed">{risk.summary}</p>
        {contributing.length > 0 && (
          <p className="mt-3 text-sm">
            <span className="font-semibold">Based on: </span>
            {contributing.join(", ")}
          </p>
        )}
        {missing.length > 0 && (
          <p className="mt-2 text-sm">
            <span className="font-semibold">Couldn&apos;t complete: </span>
            {missing.join(", ")}
          </p>
        )}
      </section>

      <section aria-labelledby="listing-heading" className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <h2 id="listing-heading" className="text-base font-semibold text-slate-900">
          The listing you checked
        </h2>
        <p className="mt-1 text-slate-700">
          {dollars(listing.rent_cents)}/month · {listing.bedrooms === 0 ? "Studio" : `${listing.bedrooms} bedroom${listing.bedrooms === 1 ? "" : "s"}`} ·{" "}
          {listing.address_text}, {listing.city} · {check.photos.length} photo
          {check.photos.length === 1 ? "" : "s"}
          {check.contacts.length > 0 && ` · ${check.contacts.length} contact detail${check.contacts.length === 1 ? "" : "s"}`}
        </p>
      </section>

      <section aria-labelledby="results-heading" className="flex flex-col gap-4">
        <h2 id="results-heading" className="text-xl font-semibold">
          What we checked and found
        </h2>
        {ordered(check.signals).map((s) => (
          <SignalCard key={s.name} result={s} />
        ))}
      </section>

      {related && related.total > 0 && (
        <section aria-labelledby="related-heading" className="flex flex-col gap-3">
          <h2 id="related-heading" className="text-xl font-semibold">
            Linked listings
          </h2>
          <p className="text-slate-700">{related.explanation}</p>
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {related.checks.map((r) => (
              <li key={r.check} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
                <span className="text-slate-900">
                  <span className="font-mono text-slate-600">#{r.check}</span> · {r.city} · checked{" "}
                  {r.created_on}
                </span>
                <span className="flex flex-wrap gap-1.5">
                  {r.linked_by.map((l) => (
                    <span key={l} className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-700">
                      same {l}
                    </span>
                  ))}
                  {r.same_address && (
                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-900">
                      same address
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-sm text-slate-600">{related.next_step}</p>
        </section>
      )}

      <section aria-labelledby="steps-heading" className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <h2 id="steps-heading" className="text-xl font-semibold">
          Before you pay anything
        </h2>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-slate-800">
          {SAFE_STEPS.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </section>

      <p>
        <Link
          href="/"
          className="inline-flex rounded-lg border border-slate-300 bg-white px-4 py-2 font-medium text-slate-900 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        >
          Check another listing
        </Link>
      </p>
    </div>
  );
}
