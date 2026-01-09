import { PdfFile } from "@/app/lib/pdf";

export default function PdfFileChip({ file }: { file: PdfFile }) {
  return (
    <div className="
      inline-flex items-center gap-2
      rounded-full bg-[#1f1f1f]
      px-3 py-1 text-xs text-gray-200
      border border-white/10
    ">
      <span>📄</span>
      <span className="max-w-[160px] truncate">{file.name}</span>
      <span className="text-gray-400">
        {(file.size / 1024 / 1024).toFixed(1)} MB
      </span>
    </div>
  );
}
