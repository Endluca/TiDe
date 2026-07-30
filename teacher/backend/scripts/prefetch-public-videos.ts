import { runVideoPrefetchEvent } from './video-prefetch-event';

const forwardedArguments = process.argv
  .slice(2)
  .filter((value) => !value.startsWith('--area=') && value !== '--wait');

if (!forwardedArguments.some((value) => value.startsWith('--trigger='))) {
  forwardedArguments.push('--trigger=camp-launch');
}

void runVideoPrefetchEvent(forwardedArguments).catch((error: unknown) => {
  console.error(
    `CDN 预热失败：${error instanceof Error ? error.message : String(error)}`,
  );
  process.exitCode = 1;
});
