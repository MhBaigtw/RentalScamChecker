"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { submitCheck, type FieldErrors } from "@/lib/api";

import PhotoPicker, { type PickedPhoto } from "./PhotoPicker";

// Field names match POST /checks (docs/api-design.md).
const FIELD_LABELS: Record<string, string> = {
  listing_text: "Listing text",
  monthly_rent: "Monthly rent",
  bedrooms: "Bedrooms",
  unit_type: "What is for rent",
  address_text: "Address",
  city: "City",
  submitted_url: "Link to the listing",
  contact_phone: "Phone number",
  contact_email: "Email",
  photos: "Photos",
};

const inputClass =
  "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-600/30 aria-[invalid=true]:border-orange-600";

function Field({
  name,
  label,
  hint,
  error,
  children,
}: {
  name: string;
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={name} className="block font-medium text-slate-900">
        {label}
      </label>
      {hint && (
        <p id={`${name}-hint`} className="mt-0.5 text-sm text-slate-600">
          {hint}
        </p>
      )}
      {children}
      {error && (
        <p id={`${name}-error`} className="mt-1 text-sm font-medium text-orange-800">
          {error}
        </p>
      )}
    </div>
  );
}

function describedBy(name: string, hint: boolean, errors: FieldErrors): string | undefined {
  const ids = [hint ? `${name}-hint` : null, errors[name] ? `${name}-error` : null].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
}

export default function CheckForm() {
  const router = useRouter();
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const summaryRef = useRef<HTMLDivElement>(null);

  function showProblems(text: string, fields: FieldErrors) {
    setMessage(text);
    setErrors(fields);
    // Move focus to the summary so keyboard and screen reader users hear what went wrong.
    requestAnimationFrame(() => summaryRef.current?.focus());
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (photos.length === 0) {
      showProblems("Please fix the following:", { photos: "Add at least one photo." });
      return;
    }
    const form = new FormData(event.currentTarget);
    form.delete("photo_input");
    for (const p of photos) form.append("photos", p.file, p.file.name);
    setSubmitting(true);
    const result = await submitCheck(form);
    if (result.kind === "created") {
      router.push(`/checks/${result.checkId}`);
      return;
    }
    setSubmitting(false);
    if (result.kind === "invalid") showProblems("Please fix the following:", result.fields);
    else showProblems(result.message, {});
  }

  const invalid = (name: string) => (errors[name] ? true : undefined);
  const errorEntries = Object.entries(errors);

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      aria-labelledby="form-heading"
      className="flex flex-col gap-8 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2 id="form-heading" className="text-xl font-semibold">
        Listing details
      </h2>

      {message && (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          className="rounded-lg border border-orange-200 bg-orange-50 p-4 text-orange-950 focus:outline-2 focus:outline-orange-700"
        >
          <p className="font-medium">{message}</p>
          {errorEntries.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {errorEntries.map(([field, text]) => (
                <li key={field}>
                  <a href={`#${field === "photos" ? "photo_input" : field}`} className="underline">
                    {FIELD_LABELS[field] ?? field}
                  </a>
                  : {text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <fieldset className="flex flex-col gap-5">
        <legend className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          The listing
        </legend>
        <Field
          name="listing_text"
          label="Listing text"
          hint="Copy and paste the full description. Phone numbers and emails in it are replaced before it is stored."
          error={errors.listing_text}
        >
          <textarea
            id="listing_text"
            name="listing_text"
            required
            rows={7}
            className={inputClass}
            aria-invalid={invalid("listing_text")}
            aria-describedby={describedBy("listing_text", true, errors)}
          />
        </Field>
        <Field
          name="submitted_url"
          label="Link to the listing (optional)"
          hint="Saved with your check for reference. We never open or download it."
          error={errors.submitted_url}
        >
          <input
            id="submitted_url"
            name="submitted_url"
            type="url"
            inputMode="url"
            placeholder="https://"
            className={inputClass}
            aria-invalid={invalid("submitted_url")}
            aria-describedby={describedBy("submitted_url", true, errors)}
          />
        </Field>
      </fieldset>

      <fieldset className="flex flex-col gap-5">
        <legend className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Rent and place
        </legend>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field name="monthly_rent" label="Monthly rent ($)" error={errors.monthly_rent}>
            <input
              id="monthly_rent"
              name="monthly_rent"
              required
              inputMode="decimal"
              placeholder="1850"
              className={inputClass}
              aria-invalid={invalid("monthly_rent")}
              aria-describedby={describedBy("monthly_rent", false, errors)}
            />
          </Field>
          <Field name="bedrooms" label="Bedrooms" error={errors.bedrooms}>
            <select
              id="bedrooms"
              name="bedrooms"
              required
              defaultValue="1"
              className={inputClass}
              aria-invalid={invalid("bedrooms")}
              aria-describedby={describedBy("bedrooms", false, errors)}
            >
              <option value="0">Studio</option>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <fieldset>
          <legend className="font-medium text-slate-900">What is for rent</legend>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:gap-6">
            {[
              ["whole_unit", "A whole unit"],
              ["room", "A room in a shared place"],
              ["basement", "A basement apartment"],
            ].map(([value, text]) => (
              <label key={value} className="flex items-center gap-2 text-slate-800">
                <input
                  type="radio"
                  name="unit_type"
                  value={value}
                  defaultChecked={value === "whole_unit"}
                  className="h-4 w-4 accent-teal-700"
                />
                {text}
              </label>
            ))}
          </div>
          {errors.unit_type && (
            <p className="mt-1 text-sm font-medium text-orange-800">{errors.unit_type}</p>
          )}
        </fieldset>
        <div className="grid gap-5 sm:grid-cols-[2fr_1fr]">
          <Field
            name="address_text"
            label="Address or nearest intersection"
            error={errors.address_text}
          >
            <input
              id="address_text"
              name="address_text"
              required
              autoComplete="off"
              placeholder="1430 Trafalgar Rd"
              className={inputClass}
              aria-invalid={invalid("address_text")}
              aria-describedby={describedBy("address_text", false, errors)}
            />
          </Field>
          <Field name="city" label="City" error={errors.city}>
            <input
              id="city"
              name="city"
              required
              autoComplete="off"
              placeholder="Oakville"
              className={inputClass}
              aria-invalid={invalid("city")}
              aria-describedby={describedBy("city", false, errors)}
            />
          </Field>
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-5">
        <legend className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Contact details (optional)
        </legend>
        <p className="-mt-2 text-sm text-slate-600">
          If the listing gives a phone number or email, add it: we check whether it appears in
          other listings. We keep only a one-way fingerprint, never the number or address itself.
        </p>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field name="contact_phone" label="Phone number" error={errors.contact_phone}>
            <input
              id="contact_phone"
              name="contact_phone"
              type="tel"
              autoComplete="off"
              className={inputClass}
              aria-invalid={invalid("contact_phone")}
              aria-describedby={describedBy("contact_phone", false, errors)}
            />
          </Field>
          <Field name="contact_email" label="Email" error={errors.contact_email}>
            <input
              id="contact_email"
              name="contact_email"
              type="email"
              autoComplete="off"
              className={inputClass}
              aria-invalid={invalid("contact_email")}
              aria-describedby={describedBy("contact_email", false, errors)}
            />
          </Field>
        </div>
      </fieldset>

      <PhotoPicker photos={photos} onChange={setPhotos} error={errors.photos} />

      <div className="flex flex-col gap-3 border-t border-slate-200 pt-6 sm:flex-row sm:items-center">
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex items-center justify-center rounded-lg bg-teal-700 px-5 py-3 text-base font-semibold text-white shadow-sm hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-wait disabled:opacity-70"
        >
          {submitting ? "Sending…" : "Check this listing"}
        </button>
        <p className="text-sm text-slate-600">Most checks finish in under a minute.</p>
      </div>
    </form>
  );
}
