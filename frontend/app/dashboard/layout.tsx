// frontend/app/dashboard/layout.tsx

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-blue-500/30">
      <main className="p-6">{children}</main>
    </div>
  );
}
