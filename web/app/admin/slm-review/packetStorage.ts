const DB_NAME = "radon-slm-review";
const STORE_NAME = "packets";

function openPacketStore(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Could not open browser storage."));
  });
}

export async function saveReviewPacket(reviewer: string, packet: unknown): Promise<void> {
  const db = await openPacketStore();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE_NAME, "readwrite");
      transaction.objectStore(STORE_NAME).put(packet, reviewer);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error ?? new Error("Could not save review packet."));
      transaction.onabort = () => reject(transaction.error ?? new Error("Could not save review packet."));
    });
  } finally {
    db.close();
  }
}

export async function loadReviewPacket(reviewer: string): Promise<unknown | null> {
  const db = await openPacketStore();
  try {
    return await new Promise<unknown | null>((resolve, reject) => {
      const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(reviewer);
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(request.error ?? new Error("Could not restore review packet."));
    });
  } finally {
    db.close();
  }
}
