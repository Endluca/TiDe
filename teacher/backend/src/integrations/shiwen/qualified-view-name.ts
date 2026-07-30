const QUALIFIED_VIEW_PATTERN =
  /^(?<schema>[a-z_][a-z0-9_]*)\.(?<view>[a-z_][a-z0-9_]*)$/;

export function quoteQualifiedViewName(value: string): string {
  const match = QUALIFIED_VIEW_PATTERN.exec(value);

  if (!match?.groups) {
    throw new Error('安全视图名称必须使用 schema.view 格式');
  }

  return `"${match.groups.schema}"."${match.groups.view}"`;
}
