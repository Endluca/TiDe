export class ShiwenReadError extends Error {}

export class ShiwenViewNotConfiguredError extends ShiwenReadError {
  constructor(viewKey: string) {
    super(`世文安全视图尚未配置：${viewKey}`);
    this.name = 'ShiwenViewNotConfiguredError';
  }
}

export class ShiwenSourceUnavailableError extends ShiwenReadError {
  constructor(operation: string, cause?: unknown) {
    super(`世文数据源暂时不可用：${operation}`, { cause });
    this.name = 'ShiwenSourceUnavailableError';
  }
}

export class ShiwenContractError extends ShiwenReadError {
  constructor(operation: string, cause?: unknown) {
    super(`世文安全视图数据不符合合同：${operation}`, { cause });
    this.name = 'ShiwenContractError';
  }
}
