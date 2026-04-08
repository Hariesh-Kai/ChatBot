"use client";

const MAX_PDF_SIZE_MB = 50;

export function getFirstPdfFile(files: FileList | File[] | null | undefined): File | null {
  if (!files) return null;
  const list = Array.from(files);
  return list[0] ?? null;
}

export function validatePdfFile(file: File | null | undefined): string | null {
  if (!file) return "No file selected";

  const type = String(file.type || "").toLowerCase();
  const name = String(file.name || "").toLowerCase();
  const isPdf = type === "application/pdf" || name.endsWith(".pdf");

  if (!isPdf) {
    return "Only PDF files are supported";
  }

  if (file.size > MAX_PDF_SIZE_MB * 1024 * 1024) {
    return `PDF must be smaller than ${MAX_PDF_SIZE_MB}MB`;
  }

  return null;
}

export { MAX_PDF_SIZE_MB };
