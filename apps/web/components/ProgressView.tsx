import type { Check } from "@/lib/api";
import { signalInfo } from "@/lib/signals";

export default function ProgressView({ check }: { check: Check }) {
  const done = new Map(check.signals.map((s) => [s.name, s]));
  const names = check.expected_signals.length
    ? check.expected_signals
    : check.signals.map((s) => s.name);
  const finished = names.filter((n) => done.has(n)).length;
  const percent = names.length ? Math.round((finished / names.length) * 100) : 0;

  return (
    <section aria-labelledby="progress-heading" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 id="progress-heading" className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Checking your listing
        </h1>
        <p className="text-slate-700">
          Each check runs on its own. You can keep this page open; the report appears here when
          they are done.
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <p aria-live="polite" className="font-medium text-slate-900">
          {finished} of {names.length} checks done
        </p>
        <div
          role="progressbar"
          aria-label="Checks done"
          aria-valuemin={0}
          aria-valuemax={names.length}
          aria-valuenow={finished}
          className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"
        >
          <div
            className="h-full rounded-full bg-teal-600 transition-[width] duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>
        <ul className="mt-5 divide-y divide-slate-100">
          {names.map((name) => {
            const result = done.get(name);
            const info = signalInfo(name);
            return (
              <li key={name} className="flex items-start justify-between gap-4 py-3">
                <div>
                  <p className="font-medium text-slate-900">{info.title}</p>
                  <p className="text-sm text-slate-600">{info.checks}</p>
                </div>
                {result ? (
                  <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-sm text-slate-700">
                    Done
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-2 text-sm text-slate-600">
                    <span
                      aria-hidden="true"
                      className="h-3 w-3 animate-spin rounded-full border-2 border-teal-600 border-t-transparent"
                    />
                    Checking
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
      <p className="text-sm text-slate-600">
        Results are shown together at the end, so you see the full picture at once.
      </p>
    </section>
  );
}
