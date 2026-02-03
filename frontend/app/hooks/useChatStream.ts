// import { useState, useCallback } from "react";
// import { StreamParser } from "@/app/lib/stream-parser";
// import { Message } from "@/app/lib/types";

// export function useChatStream(onUpdate: (updater: (prev: Message[]) => Message[]) => void) {
//   const [isStreaming, setIsStreaming] = useState(false);

//   const startStream = useCallback(async (response: Response, sessionId: string) => {
//     const reader = response.body?.getReader();
//     if (!reader) return;

//     const decoder = new TextDecoder();
//     const parser = new StreamParser();
//     setIsStreaming(true);

//     // 1. Add a placeholder AI message
//     onUpdate((prev) => [
//       ...prev,
//       {
//         id: crypto.randomUUID(),
//         role: "assistant",
//         content: "",
//         status: "streaming",
//         timestamp: new Date().toISOString(),
//       },
//     ]);

//     try {
//       while (true) {
//         const { done, value } = await reader.read();
//         if (done) break;

//         const chunk = decoder.decode(value, { stream: true });
//         const frames = parser.push(chunk);

//         frames.forEach((frame) => {
//           onUpdate((prev) => {
//             const last = [...prev];
//             const aiMsg = last[last.length - 1];

//             if (frame.type === "text") {
//               // Standard LLM text
//               aiMsg.content += frame.value;
//             } else if (frame.type === "event") {
//               // 🔥 THIS IS WHERE RAG HAPPENS
//               const event = frame.value;
              
//               if (event.type === "MODEL_STAGE") {
//                 // Update a sub-status field so UI shows "Searching..."
//                 aiMsg.status = "streaming"; 
//                 aiMsg.meta = { ...aiMsg.meta, currentStage: event.message };
//               }
              
//               if (event.type === "SOURCES") {
//                 // Store the RAG sources in the message object
//                 aiMsg.sources = event.sources;
//               }
//             }
//             return last;
//           });
//         });
//       }
//     } finally {
//       setIsStreaming(false);
//       onUpdate((prev) => {
//         const last = [...prev];
//         last[last.length - 1].status = "sent";
//         return last;
//       });
//     }
//   }, [onUpdate]);

//   return { startStream, isStreaming };
// }