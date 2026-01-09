// app/layout.tsx
import "./globals.css";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // 🔥 Ensure suppressHydrationWarning is here
    <html lang="en" suppressHydrationWarning>
      <body
        suppressHydrationWarning
        className="h-screen overflow-hidden bg-black text-white"
      >
        {children}
      </body>
    </html>
  );
}
