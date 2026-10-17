import type { Metadata } from "next";
import { Suspense } from "react";

import CheckView from "@/components/CheckView";

export const metadata: Metadata = {
  title: "Your check · RentalScamChecker",
  // Each check URL works as a private link to a submission: keep it out of search engines.
  robots: { index: false, follow: false },
};

// params is awaited inside Suspense so the page shell can prerender (Cache Components).
async function CheckFromParams({ params }: Pick<PageProps<"/checks/[id]">, "params">) {
  const { id } = await params;
  return <CheckView id={id} />;
}

export default function CheckPage({ params }: PageProps<"/checks/[id]">) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
      <Suspense fallback={<p className="text-slate-600">Loading your check…</p>}>
        <CheckFromParams params={params} />
      </Suspense>
    </div>
  );
}
