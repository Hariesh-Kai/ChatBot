import Image from "next/image";

interface BrandProps {
  iconOnly?: boolean;
}

export default function Brand({ iconOnly = false }: BrandProps) {
  const logoSize = iconOnly ? 32 : 32;
  return (
    <div className={`group relative flex items-center ${iconOnly ? "" : "cursor-pointer gap-3 px-4 py-3"}`}>
      {/* Logo wrapper */}
      <div
        className={`
          relative
          transition-transform duration-300 ease-out
          ${iconOnly ? "group-hover:scale-[1.03]" : "group-hover:scale-105"}
          ${
            iconOnly
              ? "flex h-9 w-9 items-center justify-center"
              : ""
          }
        `}
      >
        {/* Glow layer */}
        {!iconOnly && (
          <div
            className="
              absolute inset-0 rounded-full
              opacity-0 group-hover:opacity-100
              transition-opacity duration-300
              blur-md
              bg-white/40
            "
          />
        )}

        {/* Logo */}
        <Image
          src="/chat-ui-logo.svg"
          alt="CHAT UI"
          width={logoSize}
          height={logoSize}
          className={`
            relative z-10
            object-contain
            transition-opacity duration-300
            group-hover:opacity-100
            ${
              iconOnly
                ? "h-8 w-8 opacity-100"
                : "opacity-90"
            }
          `}
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
            CHAT UI
          </span>
          <span className="text-[11px] text-gray-400">
            AI Document Assistant
          </span>
        </div>
      )}
    </div>
  );
}
