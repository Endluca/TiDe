export async function loadTaskContexts(items, loadTask, signal, concurrency = 3) {
  const results = new Array(items.length);
  const workerCount = Math.min(
    items.length,
    Math.max(1, Math.floor(Number(concurrency)) || 1),
  );
  let nextIndex = 0;

  const worker = async () => {
    while (!signal?.aborted) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;

      try {
        const value = await loadTask(items[index].taskInstanceId, signal);
        results[index] = { status: "fulfilled", value };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  };

  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}
