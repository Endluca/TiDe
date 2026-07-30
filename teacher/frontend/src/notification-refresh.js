const requestKey = (input) => JSON.stringify(input);

export function createNotificationRequestQueue(request) {
  let active = null;
  const pending = [];
  const entries = new Map();

  const startNext = () => {
    const entry = pending.shift();
    if (!entry) {
      active = null;
      return;
    }

    active = entry;
    Promise.resolve()
      .then(() => request(entry.input))
      .then(
        (result) => {
          entries.delete(entry.key);
          active = null;
          startNext();
          entry.resolve(result);
        },
        (error) => {
          entries.delete(entry.key);
          active = null;
          startNext();
          entry.reject(error);
        },
      );
  };

  return {
    run(input) {
      const key = requestKey(input);
      const existing = entries.get(key);
      if (existing) return existing.promise;

      let resolve;
      let reject;
      const promise = new Promise((onResolve, onReject) => {
        resolve = onResolve;
        reject = onReject;
      });
      const entry = { key, input, promise, resolve, reject };
      entries.set(key, entry);
      pending.push(entry);
      if (!active) startNext();
      return promise;
    },
    isRunning() {
      return active !== null || pending.length > 0;
    },
  };
}
