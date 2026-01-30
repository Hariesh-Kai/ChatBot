// frontend/app/components/chat/FeedbackBar.tsx

"use client";

import { useState } from "react";
import { submitFeedback, FeedbackLabel } from "@/app/lib/feedback";
import { Message } from "@/app/lib/types";

/* ================================
   PROPS
================================ */

interface FeedbackBarProps {
  message: Message;
  sessionId: string;

  companyDocumentId?: string;
  revisionNumber?: number;

  question?: string;
  chunkIds?: string[];
}

/* ================================
   COMPONENT
================================ */

export default function FeedbackBar({
  message,
  sessionId,
  companyDocumentId,
  revisionNumber,
  question,
  chunkIds,
}: FeedbackBarProps) {
  const [submitted, setSubmitted] = useState<FeedbackLabel | null>(null);

  if (submitted) {
    return (
      <div className="mt-2 text-xs text-gray-500">
        Feedback recorded ✓
      </div>
    );
  }

  const sendFeedback = async (label: FeedbackLabel) => {
    setSubmitted(label);

    await submitFeedback({
      session_id: sessionId,
      company_document_id: companyDocumentId,
      revision_number: revisionNumber?.toString(),
      question: question || "",
      answer: message.content || "",
      feedback_label: label,
      chunk_ids: chunkIds,
    });
  };

  return (
    <div className="mt-2 flex items-center gap-2 text-sm text-gray-400">
      <button
        className="hover:text-green-400 transition"
        onClick={() => sendFeedback("correct")}
        title="Correct"
      >
        👍
      </button>

      <button
        className="hover:text-yellow-400 transition"
        onClick={() => sendFeedback("partial")}
        title="Partially correct"
      >
        ⚠️
      </button>

      <button
        className="hover:text-red-400 transition"
        onClick={() => sendFeedback("incorrect")}
        title="Incorrect"
      >
        
      </button>

      <button
        className="hover:text-purple-400 transition"
        onClick={() => sendFeedback("hallucination")}
        title="Hallucination"
      >
        
      </button>
    </div>
  );
}
