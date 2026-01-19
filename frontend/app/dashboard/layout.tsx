// frontend/app/dashboard/layout.tsx

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-blue-500/30">
      <nav className="border-b border-white/10 bg-[#111] px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-blue-500">🛠️ Developer Dashboard</span>
            <span className="rounded bg-white/10 px-2 py-0.5 text-xs text-gray-400">v1.0</span>
          </div>
          <a href="/" className="text-sm text-gray-400 hover:text-white hover:underline">
            ← Back to Chat
          </a>
        </div>
      </nav>
      <main className="p-6">{children}</main>
    </div>
  );
}