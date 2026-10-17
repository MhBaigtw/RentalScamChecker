"use client";

import { useEffect, useRef } from "react";

export const MAX_PHOTOS = 10;
const ACCEPT = "image/jpeg,image/png,image/webp";

export type PickedPhoto = { id: string; file: File; url: string };

let nextId = 0;

export default function PhotoPicker({
  photos,
  onChange,
  error,
}: {
  photos: PickedPhoto[];
  onChange: (photos: PickedPhoto[]) => void;
  error?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const latest = useRef(photos);

  useEffect(() => {
    latest.current = photos;
  }, [photos]);

  // Free the preview URLs when the form goes away.
  useEffect(() => () => latest.current.forEach((p) => URL.revokeObjectURL(p.url)), []);

  function add(files: FileList | null) {
    if (!files) return;
    const room = MAX_PHOTOS - photos.length;
    const added = Array.from(files)
      .slice(0, room)
      .map((file) => ({ id: `photo-${nextId++}`, file, url: URL.createObjectURL(file) }));
    onChange([...photos, ...added]);
    if (inputRef.current) inputRef.current.value = "";
  }

  function remove(id: string) {
    const photo = photos.find((p) => p.id === id);
    if (photo) URL.revokeObjectURL(photo.url);
    onChange(photos.filter((p) => p.id !== id));
  }

  const full = photos.length >= MAX_PHOTOS;

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Photos
      </legend>
      <p id="photo-hint" className="-mt-2 text-sm text-slate-600">
        Add 1 to {MAX_PHOTOS} photos from the listing (JPEG, PNG or WebP, up to 10 MB each). Save
        them from the listing first. Location data is removed when they are stored.
      </p>
      <div>
        <label
          htmlFor="photo_input"
          className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-400 bg-slate-50 px-4 py-3 font-medium text-slate-800 hover:border-teal-600 hover:bg-teal-50 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-teal-700 ${full ? "pointer-events-none opacity-60" : ""}`}
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 text-teal-700">
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              d="M12 5v14M5 12h14"
            />
          </svg>
          {photos.length ? "Add more photos" : "Choose photos"}
          <input
            ref={inputRef}
            id="photo_input"
            name="photo_input"
            type="file"
            accept={ACCEPT}
            multiple
            disabled={full}
            className="sr-only"
            aria-describedby={error ? "photo-hint photo-error" : "photo-hint"}
            aria-invalid={error ? true : undefined}
            onChange={(e) => add(e.target.files)}
          />
        </label>
        <span className="ml-3 text-sm text-slate-600" aria-live="polite">
          {photos.length} of {MAX_PHOTOS} added
        </span>
      </div>
      {error && (
        <p id="photo-error" className="text-sm font-medium text-orange-800">
          {error}
        </p>
      )}
      {photos.length > 0 && (
        <ul className="grid grid-cols-3 gap-3 sm:grid-cols-5" aria-label="Selected photos">
          {photos.map((p, index) => (
            <li key={p.id} className="relative">
              {/* Local previews (object URLs), so next/image optimisation doesn't apply. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={p.url}
                alt={`Photo ${index + 1}: ${p.file.name}`}
                className="aspect-square w-full rounded-md border border-slate-200 object-cover"
              />
              <button
                type="button"
                onClick={() => remove(p.id)}
                aria-label={`Remove photo ${index + 1}`}
                className="absolute right-1 top-1 rounded-full bg-white/95 p-1 text-slate-700 shadow ring-1 ring-slate-300 hover:text-slate-950 focus-visible:outline-2 focus-visible:outline-teal-700"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
                  <path
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    d="m6 6 12 12M18 6 6 18"
                  />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
