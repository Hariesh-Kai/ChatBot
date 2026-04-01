// frontend/app/dashboard/layout.tsx

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="h-screen overflow-y-auto bg-black text-white font-sans selection:bg-blue-500/30">
      <main className="min-h-full p-6 pb-10">{children}</main>
    </div>
  );
}
