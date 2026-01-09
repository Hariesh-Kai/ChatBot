import Image from "next/image";

interface BrandProps {
  iconOnly?: boolean;
}

export default function Brand({ iconOnly = false }: BrandProps) {
  return (
    <div
      className="
        group relative flex items-center gap-3 px-4 py-3
        cursor-pointer
      "
    >
      {/* Logo wrapper */}
      <div
        className="
          relative
          transition-transform duration-300 ease-out
          group-hover:scale-105
        "
      >
        {/* Glow layer */}
        <div
          className="
            absolute inset-0 rounded-full
            opacity-0 group-hover:opacity-100
            transition-opacity duration-300
            blur-md
            bg-white/40
          "
        />

        {/* Logo */}
        <Image
          src="/kavin-logo.svg"
          alt="KAVIN"
          width={26}
          height={26}
          className="
            relative z-10
            opacity-90
            transition-opacity duration-300
            group-hover:opacity-100
          "
        />
      </div>

      {/* Text (expanded only) */}
      {!iconOnly && (
        <div className="flex flex-col leading-tight">
          <span
            className="
              text-sm font-semibold text-white
              transition-colors duration-300
              group-hover:text-white
            "
          >
            KAVIN
          </span>
          <span className="text-[11px] text-gray-400">
            AI Document Assistant
          </span>
        </div>
      )}
    </div>
  );
}
