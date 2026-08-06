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
  assert.equal(component.includes('Course ID'), false);
  assert.equal(component.includes('course(s)'), false);
  assert.match(component, /hasMultipleCourses &&/);
  assert.match(component, /role="tablist"/);
  assert.match(component, /visitedCourseIds/);
  assert.match(component, /hidden=\{!isActive\}/);
  assert.match(component, /kuozhi-embed-shell--hide-navigation/);
  assert.equal(component.includes('/complete'), false);
});

test('Kuozhi iframe navigation is cropped without touching cross-origin content', async () => {
  const styles = await readFile(
    new URL(
      'frontend/src/features/task-content/kuozhi-course-task.css',
      repoRoot,
    ),
    'utf8',
  );

  assert.match(styles, /--kuozhi-navigation-height:\s*72px/);
  assert.match(styles, /translateY\(calc\(-1 \* var\(--kuozhi-navigation-height\)\)\)/);
});
