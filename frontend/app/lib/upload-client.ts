// frontend/app/lib/upload-client.ts

import { API_BASE } from "./config";

interface UploadOptions {
  file: File;
  sessionId: string;
  onProgress: (percent: number) => void;
}

export function uploadPdfWithProgress({
  file,
  sessionId,
  onProgress,
}: UploadOptions): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    
    formData.append("file", file);
    formData.append("session_id", sessionId);

    // 1. TRACK REAL UPLOAD BYTES
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = (event.loaded / event.total) * 100;
        onProgress(percent);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const json = JSON.parse(xhr.responseText);
          resolve(json);
        } catch (e) {
          reject(new Error("Invalid server response"));
        }
      } else {
        reject(new Error(xhr.responseText || "Upload failed"));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload"));

    xhr.open("POST", `${API_BASE}/upload/`);
    xhr.send(formData);
  });
}