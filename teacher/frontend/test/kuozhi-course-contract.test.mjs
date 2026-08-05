import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const repoRoot = new URL('../../', import.meta.url);

test('Kuozhi launch and progress stay behind the authenticated backend', async () => {
  const api = await readFile(
    new URL('frontend/src/api/kuozhi-api.js', repoRoot),
    'utf8',
  );
  const component = await readFile(
    new URL(
      'frontend/src/features/task-content/KuozhiCourseTask.jsx',
      repoRoot,
    ),
    'utf8',
  );

  assert.match(api, /\/api\/v1\/tasks\/\$\{taskInstanceId\}\/kuozhi-launch/);
  assert.match(api, /\/api\/v1\/tasks\/\$\{taskInstanceId\}\/kuozhi-progress/);
  assert.match(api, /kuozhi-progress\/refresh/);
  assert.match(api, /Idempotency-Key/);
  assert.equal(component.includes('KUOZHI_SECRET_KEY'), false);
  assert.match(component, /<iframe/);
  assert.match(component, /allowFullScreen/);
  assert.equal(component.includes('target="_blank"'), false);
  assert.equal(component.includes('Open course'), false);
  assert.equal(component.includes('Open in new window'), false);
  assert.equal(component.includes('course.embedMode'), false);
  assert.match(component, /SAMPLE_DRY_RUN/);
  assert.match(component, /launchResponse\.dataMode === 'REAL'/);
  assert.match(component, /不会修改当前老师的任务状态/);
  assert.equal(component.includes('/complete'), false);
});
