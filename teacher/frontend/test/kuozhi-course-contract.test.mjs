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
  const progressCard = await readFile(
    new URL(
      'frontend/src/features/task-content/KuozhiProgressCard.jsx',
      repoRoot,
    ),
    'utf8',
  );
  const app = await readFile(
    new URL('frontend/src/App.jsx', repoRoot),
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
  assert.match(component, /onProgressStateChange/);
  assert.match(component, /launchError,/);
  assert.match(component, /canRefresh:/);
  assert.match(component, /setLaunch\(launchResponse\)[\s\S]*getKuozhiProgress/);
  assert.match(component, /progressError=\{launchError \|\| progressError\}/);
  assert.match(component, /kuozhi-progress-card--mobile/);
  assert.equal(component.includes('kuozhi-progress-panel'), false);
  assert.match(progressCard, /kuozhi-progress-course-item/);
  assert.match(progressCard, /defaultOpen=\{courses\.length === 1/);
  assert.match(progressCard, /const requiredCourseTasks = courseTasks\.filter/);
  assert.match(progressCard, /const requiredTasks = tasks\.filter/);
  assert.match(progressCard, /if \(progressError\)/);
  assert.match(progressCard, /!progressError && courses\.length === 0/);
  assert.match(progressCard, /courseTask\.type === 'TESTPAPER'/);
  assert.match(progressCard, /kuozhi-task-score/);
  assert.match(progressCard, /courseTask\.score/);
  assert.match(app, /kuozhi-progress-card--sidebar/);
  assert.match(app, /canRefresh=\{kuozhiProgressState\?\.canRefresh\}/);
  assert.match(app, /kuozhiProgressState\?\.launchError \|\| kuozhiProgressState\?\.progressError/);
  assert.equal(app.includes('Help Center'), false);
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
  assert.match(styles, /\.kuozhi-progress-course-list[\s\S]*overflow-y:\s*scroll/);
  assert.match(styles, /scrollbar-gutter:\s*stable/);
});
