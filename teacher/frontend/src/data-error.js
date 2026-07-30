const DEFAULT_RATE_LIMIT_RETRY_SECONDS = 10;

const isRateLimitError = (error) => (
  error?.status === 429
  || error?.code === "RATE_LIMITED"
  || /throttl|too many requests|rate.?limit/i.test(error?.message || "")
);

export function describeDataError(error, language) {
  if (isRateLimitError(error)) {
    const retryAfterSeconds = Math.max(
      1,
      Number(error?.retryAfterSeconds) || DEFAULT_RATE_LIMIT_RETRY_SECONDS,
    );
    return language === "zh"
      ? {
          title: "当前访问人数较多",
          message: "系统正在稍作等待，你的任务和进度不会丢失。",
          retryAfterSeconds,
        }
      : {
          title: "The service is busy right now",
          message: "Please wait a moment. Your tasks and progress are safe.",
          retryAfterSeconds,
        };
  }

  return language === "zh"
    ? {
        title: "任务数据暂时无法加载",
        message: "网络或服务暂时不稳定，请稍后重试。你的任务和进度不会丢失。",
        retryAfterSeconds: 0,
      }
    : {
        title: "Unable to load task data",
        message: "The network or service is temporarily unavailable. Your tasks and progress are safe.",
        retryAfterSeconds: 0,
      };
}
