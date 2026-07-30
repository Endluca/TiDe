const baseUrl = process.env.TIDE_SMOKE_API_URL?.replace(/\/$/, '');
const accessToken = process.env.TIDE_SMOKE_ACCESS_TOKEN;

if (!baseUrl) {
  console.error('缺少 TIDE_SMOKE_API_URL，例如 https://api.example.com');
  process.exit(1);
}

async function check(path, expectedStatuses = [200], authenticated = false) {
  const startedAt = Date.now();
  const response = await fetch(`${baseUrl}${path}`, {
    headers:
      authenticated && accessToken
        ? { authorization: `Bearer ${accessToken}` }
        : undefined,
    signal: AbortSignal.timeout(10_000),
  });
  const body = await response.text();
  if (!expectedStatuses.includes(response.status)) {
    throw new Error(
      `${path} 返回 ${response.status}，响应：${body.slice(0, 300)}`,
    );
  }
  console.log(
    `${path} ${response.status} ${Date.now() - startedAt}ms ${body.slice(0, 160)}`,
  );
}

try {
  await check('/health');
  await check('/health/ready');
  await check('/health/dependencies');

  if (accessToken) {
    await check('/api/v1/tasks', [200], true);
    await check('/api/v1/me/notifications', [200], true);
  } else {
    console.log('未设置 TIDE_SMOKE_ACCESS_TOKEN，跳过登录后接口。');
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
