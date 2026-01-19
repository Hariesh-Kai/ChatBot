// frontend/app/dashboard/page.tsx

"use client";

import { useState } from "react";
import { API_BASE } from "@/app/lib/config";
import { Play, CheckCircle, AlertTriangle, Search, FileText } from "lucide-react";

export default function DashboardPage() {
  const [activeTab, setActiveTab] = useState<"intent" | "rewrite" | "retrieve" | "health">("retrieve");

  // --- Intent State ---
  const [intentInput, setIntentInput] = useState("");
  const [intentResult, setIntentResult] = useState<any>(null);

  // --- Rewrite State ---
  const [rewriteInput, setRewriteInput] = useState("");
  const [rewriteHistory, setRewriteHistory] = useState("");
  const [rewriteResult, setRewriteResult] = useState<any>(null);

  // --- Retrieval State ---
  const [retrievalQuery, setRetrievalQuery] = useState("");
  const [retrievalDocId, setRetrievalDocId] = useState("");
  const [retrievalResult, setRetrievalResult] = useState<any>(null);

  // --- Handlers ---
  async function testIntent() {
    const res = await fetch(`${API_BASE}/devtools/intent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: intentInput }),
    });
    setIntentResult(await res.json());
  }

  async function testRewrite() {
    const historyArr = rewriteHistory.split("\n").filter(line => line.trim() !== "");
    const res = await fetch(`${API_BASE}/devtools/rewrite`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: rewriteInput, history: historyArr }),
    });
    setRewriteResult(await res.json());
  }

  async function testRetrieval() {
    const res = await fetch(`${API_BASE}/devtools/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
            question: retrievalQuery,
            company_document_id: retrievalDocId || "Agogo-1ST1", // Default for testing
            revision_number: "1"
        }),
    });
    setRetrievalResult(await res.json());
  }

  return (
    <div className="mx-auto max-w-6xl">
      
      {/* TABS */}
      <div className="mb-8 flex gap-4 border-b border-white/10 pb-1">
        <TabButton active={activeTab === "retrieve"} onClick={() => setActiveTab("retrieve")} label="RAG Retrieval" />
        <TabButton active={activeTab === "intent"} onClick={() => setActiveTab("intent")} label="Intent Classifier" />
        <TabButton active={activeTab === "rewrite"} onClick={() => setActiveTab("rewrite")} label="Query Rewriter" />
        <TabButton active={activeTab === "health"} onClick={() => setActiveTab("health")} label="System Health" />
      </div>

      {/* === TAB: RETRIEVAL === */}
      {activeTab === "retrieve" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-1 space-y-6">
                <Card title="Search Parameters">
                    <label className="block text-sm text-gray-400 mb-2">Question</label>
                    <input 
                        value={retrievalQuery}
                        onChange={(e) => setRetrievalQuery(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-4"
                        placeholder="e.g. What is the design pressure?"
                    />
                    <label className="block text-sm text-gray-400 mb-2">Document ID</label>
                    <input 
                        value={retrievalDocId}
                        onChange={(e) => setRetrievalDocId(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                        placeholder="e.g. Agogo-1ST1"
                    />
                    <button onClick={testRetrieval} className="mt-4 w-full bg-green-600 hover:bg-green-500 text-white px-4 py-2 rounded flex items-center justify-center gap-2">
                        <Search size={16} /> Run Retrieval
                    </button>
                </Card>
            </div>
            
            <div className="lg:col-span-2">
                 <Card title="Retrieved Chunks">
                    {retrievalResult ? (
                        <div className="space-y-4">
                            <div className="text-xs text-gray-500 mb-2">Found {retrievalResult.count} chunks</div>
                            {retrievalResult.chunks.map((chunk: any, i: number) => (
                                <div key={i} className="bg-[#1a1a1a] border border-white/5 rounded p-3">
                                    <div className="flex justify-between items-start mb-2">
                                        <span className="text-xs font-mono text-blue-400 bg-blue-900/20 px-2 py-0.5 rounded">
                                            Score: {chunk.score.toFixed(4)}
                                        </span>
                                        <span className="text-xs text-gray-500">{chunk.section}</span>
                                    </div>
                                    <p className="text-sm text-gray-300 whitespace-pre-wrap">{chunk.content}</p>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-gray-500 italic flex flex-col items-center justify-center h-40">
                            <FileText size={40} className="mb-2 opacity-20" />
                            Run a search to see vectors.
                        </div>
                    )}
                 </Card>
            </div>
        </div>
      )}

      {/* === TAB: INTENT === */}
      {activeTab === "intent" && (
        <div className="grid grid-cols-2 gap-8">
          <Card title="Input">
            <label className="block text-sm text-gray-400 mb-2">User Query</label>
            <input 
              value={intentInput}
              onChange={(e) => setIntentInput(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
              placeholder="e.g., Hello there"
            />
            <button onClick={testIntent} className="mt-4 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded flex items-center gap-2">
              <Play size={16} /> Test Intent
            </button>
          </Card>

          <Card title="Result">
            {intentResult ? (
              <pre className="text-sm text-green-400 overflow-auto bg-black p-4 rounded border border-white/10">
                {JSON.stringify(intentResult, null, 2)}
              </pre>
            ) : (
              <div className="text-gray-500 italic">Run a test to see results...</div>
            )}
          </Card>
        </div>
      )}

      {/* === TAB: REWRITE === */}
      {activeTab === "rewrite" && (
        <div className="grid grid-cols-2 gap-8">
          <Card title="Input Context">
            <label className="block text-sm text-gray-400 mb-2">Current Question</label>
            <input 
              value={rewriteInput}
              onChange={(e) => setRewriteInput(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-4"
              placeholder="e.g., Tell me more about it"
            />
            
            <label className="block text-sm text-gray-400 mb-2">Chat History (One message per line)</label>
            <textarea 
              value={rewriteHistory}
              onChange={(e) => setRewriteHistory(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white h-32"
              placeholder="User: What is the pressure?&#10;Assistant: 500 psi."
            />

            <button onClick={testRewrite} className="mt-4 bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded flex items-center gap-2">
              <Play size={16} /> Test Rewriter
            </button>
          </Card>

          <Card title="Result">
            {rewriteResult ? (
              <div className="space-y-4">
                <div>
                  <div className="text-xs text-gray-500 uppercase">Original</div>
                  <div className="text-gray-300">{rewriteResult.original}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Rewritten For RAG</div>
                  <div className="text-xl font-mono text-purple-400">{rewriteResult.rewritten}</div>
                </div>
              </div>
            ) : (
              <div className="text-gray-500 italic">Run a test to see results...</div>
            )}
          </Card>
        </div>
      )}

      {/* === TAB: HEALTH === */}
      {activeTab === "health" && (
        <SystemHealthCheck />
      )}

    </div>
  );
}

// --- Subcomponents ---

function TabButton({ active, onClick, label }: any) {
  return (
    <button 
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium transition-colors ${
        active ? "text-white border-b-2 border-blue-500" : "text-gray-400 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}

function Card({ title, children }: any) {
  return (
    <div className="bg-[#111] border border-white/10 rounded-xl p-6 h-full">
      <h3 className="text-lg font-medium text-white mb-4">{title}</h3>
      {children}
    </div>
  );
}

function SystemHealthCheck() {
  const [status, setStatus] = useState<any>(null);
  
  const check = async () => {
    const res = await fetch(`${API_BASE}/health`);
    setStatus(await res.json());
  }

  return (
    <Card title="Backend Status">
        <button onClick={check} className="mb-4 bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm text-white">Refresh Status</button>
        {status ? (
            <div className="grid grid-cols-3 gap-4">
                <StatusItem label="PostgreSQL" status={status.services?.postgres} />
                <StatusItem label="Redis" status={status.services?.redis} />
                <StatusItem label="MinIO" status={status.services?.minio} />
            </div>
        ) : (
            <div className="text-gray-500">Click refresh to check connections.</div>
        )}
    </Card>
  )
}

function StatusItem({ label, status }: any) {
    const isOk = status === "ok";
    return (
        <div className={`p-4 rounded border ${isOk ? "border-green-500/30 bg-green-500/10" : "border-red-500/30 bg-red-500/10"}`}>
            <div className="text-xs text-gray-400 uppercase">{label}</div>
            <div className={`text-lg font-bold flex items-center gap-2 ${isOk ? "text-green-400" : "text-red-400"}`}>
                {isOk ? <CheckCircle size={18}/> : <AlertTriangle size={18}/>}
                {status?.toUpperCase()}
            </div>
        </div>
    )
}