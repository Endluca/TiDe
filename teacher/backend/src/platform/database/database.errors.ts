export class DatabaseNotConfiguredError extends Error {
  constructor(connectionName: 'tide' | 'shiwen-read') {
    super(`数据库连接未配置：${connectionName}`);
    this.name = 'DatabaseNotConfiguredError';
  }
}
