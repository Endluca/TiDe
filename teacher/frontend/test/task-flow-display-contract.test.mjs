import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("G01 keeps shared statuses and embeds its Kuozhi assessment", async () => {
  const profileTask = await source("features/task-content/ProfileCredentialsTask.jsx");

  assert.match(profileTask, /<ExternalStatusTask task=\{sourceStatusTask\} embedded \/>/);
  assert.match(profileTask, /<KuozhiCourseTask task=\{task\}/);
  assert.doesNotMatch(profileTask, /TESOL_QUIZ|quizQuestions|Submit answers/);
});

test("task completion views use the configured task reward", async () => {
  const [taskFlow, videoQuiz] = await Promise.all([
    source("components/TaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
  ]);

  assert.match(taskFlow, /task\.tagReward/);
  assert.match(videoQuiz, /task\.tagReward/);
  assert.doesNotMatch(taskFlow, /courseAbilityTagForTask/);
  assert.doesNotMatch(videoQuiz, /courseAbilityTagForTask/);
});

test("video tasks keep the player display contract and support fullscreen playback", async () => {
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");

  assert.match(videoQuiz, /poster=\{content\.poster\}/);
  assert.match(videoQuiz, /preload="auto"/);
  assert.match(videoQuiz, /lastSavedSecondsRef\.current \+ 10/);
  assert.match(videoQuiz, /onWaiting=\{\(event\) => \{/);
  assert.match(videoQuiz, /if \(!event\.currentTarget\.paused\) setBuffering\(true\)/);
  assert.match(videoQuiz, /"视频缓冲中…"/);
  assert.match(videoQuiz, /window\.addEventListener\("pagehide", persistCurrentPosition\)/);
  assert.match(videoQuiz, /video-fullscreen-button/);
  assert.match(videoQuiz, /player\.requestFullscreen/);
  assert.match(videoQuiz, /video\.webkitEnterFullscreen/);
  assert.match(videoQuiz, /"全屏播放"/);
  assert.match(videoQuiz, /"退出全屏"/);
});

test("completed video references keep a replay entry without changing completion status", async () => {
  const [taskFlow, videoQuiz, videoStyles] = await Promise.all([
    source("components/TaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
    source("features/task-content/video-learning-task.css"),
  ]);

  assert.match(videoQuiz, /onReplayVideo/);
  assert.match(videoQuiz, /"回看视频"/);
  assert.match(videoQuiz, /"首次完整观看已完成 · 可拖动进度自由回看"/);
  assert.match(videoQuiz, /reviewMode/);
  assert.match(
    videoQuiz,
    /reviewMode \|\| task\.videoCompleted \|\| Number\(task\.videoProgress\) >= 100/,
  );
  assert.match(videoQuiz, /disabled=\{!firstViewComplete \|\| !duration\}/);
  assert.match(videoQuiz, /videoRef\.current\.currentTime = nextTime/);
  assert.match(videoQuiz, /setPointerCapture/);
  assert.match(videoQuiz, /onPointerMove=\{handleSeekPointerMove\}/);
  assert.match(videoQuiz, /firstViewComplete \? playbackProgress : videoProgress/);
  assert.match(videoQuiz, /width: `\$\{timelineProgress\}%`/);
  assert.match(videoQuiz, /const visualState = current[\s\S]*reviewMode[\s\S]*"reviewable"/);
  assert.match(videoQuiz, /disabled=\{!reviewMode\}/);
  assert.match(videoQuiz, /!reviewMode && completed \? <Check/);
  assert.doesNotMatch(videoQuiz, /disabled=\{!reviewMode \|\| !completed\}/);
  assert.match(videoStyles, /\.real-video-player \.video-progress[\s\S]*width: 100%/);
  assert.match(videoStyles, /video-seek-control::-webkit-slider-thumb/);
  assert.match(videoStyles, /\.video-seek-control:disabled[\s\S]*opacity: 0/);
  assert.match(videoStyles, /\.chapter-strip button\.reviewable/);
  assert.match(videoQuiz, /setActiveIndex\(index\)/);
  assert.match(taskFlow, /task\.status === "completed" && !reviewing/);
  assert.match(taskFlow, /onSecondary=\{hasVideo \? \(\) => setReviewing\(true\) : null\}/);
  assert.match(taskFlow, /c\("Replay video", "回看视频"\)/);
  assert.match(taskFlow, /task\.status === "completed" && reviewing/);
  assert.match(taskFlow, /<ChapterVideoLearning[\s\S]*reviewMode/);
});

test("video completion follows the saved backend step status", async () => {
  const [integratedTaskFlow, videoQuiz] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
  ]);

  assert.match(integratedTaskFlow, /refresh: refreshLatestContext/);
  assert.match(videoQuiz, /response\?\.step\?\.status !== "COMPLETED"/);
  assert.match(videoQuiz, /Math\.floor\(elapsed\) - 10/);
});

test("task details show the reason only once in the why-this-task section", async () => {
  const app = await source("App.jsx");

  assert.doesNotMatch(app, /className="task-intro"/);
  assert.match(app, /<h3>\{copy\(language, "Why this task", "为什么要做"\)\}<\/h3>/);
});

test("G04 presents three independent vertical parts and completes only at 3/3", async () => {
  const [integratedTaskFlow, readinessPhoto, deviceCheck, styles] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("features/task-content/DeviceCheckTask.jsx"),
    source("features/task-content/readiness-photo-task.css"),
  ]);

  assert.match(readinessPhoto, /<DeviceCheckTask task=\{task\} embedded forceGrandfathered=\{grandfatheredTaskCompleted\} onPassed=\{handleDevicePassed\} \/>/);
  assert.match(readinessPhoto, /completedPartCount/);
  assert.match(readinessPhoto, /<small>\/3<\/small>/);
  assert.match(readinessPhoto, /role="progressbar"/);
  assert.match(readinessPhoto, /aria-label=\{c\("Completed G04 parts", "G04 已完成模块"\)\}/);
  assert.match(readinessPhoto, /aria-valuetext=/);
  assert.match(readinessPhoto, /disabled=\{opening\} onClick=\{openCamera\}/);
  assert.match(
    readinessPhoto,
    /disabled=\{!photoFile \|\| analyzing \|\| photoApproved\} onClick=\{submit\}/,
  );
  assert.doesNotMatch(readinessPhoto, /analyzing \|\| !coursewareConfirmed/);
  assert.match(deviceCheck, /disabled=\{checking\} onClick=\{run\}/);
  assert.doesNotMatch(deviceCheck, /disabled=\{[^}]*photo|disabled=\{[^}]*courseware/);
  assert.match(readinessPhoto, /!nextDevicePassed[\s\S]*!nextPhotoApproved[\s\S]*!nextCoursewareConfirmed/);
  assert.match(integratedTaskFlow, /loadValidation: \(signal\) => getTaskValidation/);
  assert.match(readinessPhoto, /readinessPayloadFromValidation/);

  const deviceIndex = readinessPhoto.indexOf('data-g04-part="device"');
  const photoIndex = readinessPhoto.indexOf('data-g04-part="photo"');
  const coursewareIndex = readinessPhoto.indexOf('data-g04-part="courseware"');
  const captureIndex = readinessPhoto.indexOf('className="readiness-capture"');
  const submitIndex = readinessPhoto.indexOf('className="primary-button wide-button"');
  const privacyIndex = readinessPhoto.indexOf("readiness-privacy");
  const confirmationIndex = readinessPhoto.indexOf("readiness-preparation-confirm");
  assert.ok(deviceIndex >= 0);
  assert.ok(photoIndex > deviceIndex);
  assert.ok(coursewareIndex > photoIndex);
  assert.ok(captureIndex >= 0);
  assert.ok(submitIndex > captureIndex);
  assert.ok(privacyIndex > submitIndex);
  assert.ok(confirmationIndex > coursewareIndex);
  assert.match(styles, /\.g04-part-stack[\s\S]*grid-template-columns: minmax\(0, 1fr\)/);
  assert.doesNotMatch(styles, /\.g04-part-stack\s*\{[^}]*repeat\(3/);
});

test("G04 device check uses real media tracks and an uncached authenticated request", async () => {
  const [deviceCheck, tideApi] = await Promise.all([
    source("features/task-content/DeviceCheckTask.jsx"),
    source("api/tide-api.js"),
  ]);

  assert.match(deviceCheck, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(deviceCheck, /getVideoTracks\(\)\.some\(\(track\) => track\.readyState === "live"\)/);
  assert.match(deviceCheck, /getAudioTracks\(\)\.some\(\(track\) => track\.readyState === "live"\)/);
  assert.match(deviceCheck, /await probeTeacherConnection\(\)/);
  assert.doesNotMatch(deviceCheck, /navigator\.onLine/);
  assert.match(deviceCheck, /if \(onPassed\) await onPassed\(response\);[\s\S]*else await task\.execution\.submit\(\)/);
  assert.match(tideApi, /probeTeacherConnection[\s\S]*\/api\/v1\/me\/profile[\s\S]*cache: "no-store"/);
  assert.match(deviceCheck, /results: next,[\s\S]*source: "BROWSER_LOCAL",[\s\S]*checkedAt,[\s\S]*measurements:[\s\S]*network: \{ durationMs:/);
  assert.match(deviceCheck, /failed \? c\("Did not pass", "未通过"\)/);
});

test("G04 preserves an early-version completion without inventing current device results", async () => {
  const [readinessPhoto, deviceCheck] = await Promise.all([
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("features/task-content/DeviceCheckTask.jsx"),
  ]);

  assert.match(deviceCheck, /saved\?\.details\?\.checkVersion === step\.config\.version/);
  assert.match(deviceCheck, /saved\?\.details\?\.source === "BROWSER_LOCAL"/);
  assert.match(deviceCheck, /forceGrandfathered \|\| \([\s\S]*task\.taskCode === "G04"[\s\S]*task\.status === "completed"[\s\S]*!hasCurrentEvidence/);
  assert.match(deviceCheck, /grandfatheredCompleted \? \([\s\S]*device-check-grandfathered/);
  assert.match(deviceCheck, /c\("Completed under the earlier G04 version", "已按 G04 早期版本完成"\)/);
  assert.match(deviceCheck, /已完成状态已保留/);
  assert.match(deviceCheck, /不展示单项结果，也无需重新检测/);
  assert.match(deviceCheck, /grandfatheredCompleted \? null : completed \? \(/);
  assert.match(deviceCheck, /const passed = results\[key\] === "PASSED"/);
  assert.doesNotMatch(deviceCheck, /const passed = completed \|\|/);
  assert.match(readinessPhoto, /const hasCurrentThreePartCompletion = Boolean\(/);
  assert.match(readinessPhoto, /const grandfatheredTaskCompleted = taskCompleted && !hasCurrentThreePartCompletion/);
  assert.match(readinessPhoto, /coursewareProgress\?\.details\?\.checklistVersion === coursewareStep\.config\.version/);
  assert.match(readinessPhoto, /!grandfatheredTaskCompleted && \([\s\S]*role="progressbar"/);
  assert.match(readinessPhoto, /c\("Your completed status is preserved", "已完成状态继续保留"\)/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? c\("Earlier version", "早期版本"\)/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? \([\s\S]*不显示“已通过”/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? \([\s\S]*无需补做确认/);
  assert.doesNotMatch(readinessPhoto, /taskCompleted \|\| deviceProgress\?\.status === "COMPLETED"/);
  assert.doesNotMatch(readinessPhoto, /taskCompleted \|\| coursewareProgress\?\.status === "COMPLETED"/);
});

test("G04 keeps partial photo and finalization failures retryable without rolling back passed parts", async () => {
  const [integratedTaskFlow, readinessPhoto] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
  ]);

  assert.match(readinessPhoto, /const validation = persistedValidation \?\? response\?\.validation/);
  assert.match(readinessPhoto, /analysis\?\.teacherMessage/);
  assert.match(readinessPhoto, /validation\?\.resultCode === "STEPS_INCOMPLETE"/);
  assert.match(readinessPhoto, /completion\?\.status !== "COMPLETED"/);
  assert.match(readinessPhoto, /devicePassed && coursewareConfirmed && response\?\.status !== "COMPLETED"/);
  assert.match(readinessPhoto, /completedPartCount === 3 && !taskCompleted/);
  assert.match(readinessPhoto, /c\("Retry completion", "重试完成提交"\)/);
  assert.match(readinessPhoto, /void finalizeIfReady\(\{ reportTo: setCompletionError \}\)/);
  assert.doesNotMatch(readinessPhoto, /nextCoursewareConfirmed: true,[\s\S]{0,100}reportTo: setCoursewareError/);
  assert.match(integratedTaskFlow, /presentationTask\.taskCode !== "G04"/);
  assert.doesNotMatch(readinessPhoto, /setDevicePassed\(false\)|setCoursewareConfirmed\(false\)/);
});

test("G04 uses Sophia's checklist first qualified photo as its camera reference", async () => {
  const readinessPhoto = await source("features/task-content/ReadinessPhotoTask.jsx");

  assert.match(
    readinessPhoto,
    /lesson-preparation-examples\/camera-angle-good-front\.jpg/,
  );
  assert.doesNotMatch(readinessPhoto, /self-intro-reference-51talk\.webp/);
});

test("G04 shows Sophia's four visual checks with their example gallery", async () => {
  const [app, readinessPhoto, i18n] = await Promise.all([
    source("App.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("i18n.jsx"),
  ]);

  for (const criterion of ["camera_angle", "lighting", "background", "dressing"]) {
    assert.match(readinessPhoto, new RegExp(`\\["${criterion}"`));
  }
  assert.match(readinessPhoto, /<ReadinessExampleGallery initialActiveId=\{exampleFocusId\} \/>/);
  assert.doesNotMatch(readinessPhoto, /Teaching headset worn|佩戴授课耳麦|seven items|7 项/);
  assert.match(app, /four lesson-preparation checks/);
  assert.doesNotMatch(app, /all seven readiness checks|7 项准备检测结果/);
  assert.match(i18n, /三个部分可任意顺序操作/);
  assert.match(i18n, /授课画面照片的四项 AI 标准/);
  assert.doesNotMatch(i18n, /授课环境照片的七项检测|通过七项画面检测/);
});

test("all four task summary regions use Shiwen content instead of local execution steps", async () => {
  const [app, localization, i18n] = await Promise.all([
    source("App.jsx"),
    source("task-localization.js"),
    source("i18n.jsx"),
  ]);

  assert.match(app, /<div>\{task\.reason\}<\/div>/);
  assert.match(app, /<div>\{task\.result\}<\/div>/);
  assert.match(app, /<div>\{task\.standard\}<\/div>/);
  assert.match(app, /<div>\{task\.value\}<\/div>/);
  assert.doesNotMatch(app, /task\.steps\.map/);
  assert.doesNotMatch(app, /!isProfileCredentials && !isLessonPreparation/);
  assert.match(localization, /const shiwenContent = \{/);
  assert.match(localization, /\.\.\.shiwenContent/);
  assert.match(i18n, /const shiwenContent = \{/);
  assert.match(i18n, /\.\.\.shiwenContent/);
});

test("personalized task details show only Shiwen why copy without extra evidence blocks", async () => {
  const [app, styles] = await Promise.all([
    source("App.jsx"),
    source("enhancements.css"),
  ]);

  assert.match(app, /function TaskEvidenceDetails/);
  assert.match(app, /facts=\{raw\.signalFacts\}/);
  assert.match(app, /raw\.taskCategory !== "personalized" && \(\s*<TaskEvidenceDetails/);
  assert.match(app, /"Related classes", "关联课程"/);
  assert.match(app, /course\.lessonId/);
  assert.match(styles, /\.task-related-courses li/);
});

test("the task runtime no longer contains local exam views", async () => {
  const taskFlow = await source("components/TaskFlow.jsx");
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");
  const profileQuiz = await source("features/task-content/ProfileCredentialsTask.jsx");
  const integratedTaskFlow = await source("components/IntegratedTaskFlow.jsx");

  for (const runtime of [taskFlow, videoQuiz, profileQuiz, integratedTaskFlow]) {
    assert.doesNotMatch(runtime, /QUIZ|quizQuestions|passScore|Correct answer:|Submit answers/);
  }
});

test("teacher-facing task copy hides internal codes and prototype labels", async () => {
  const profileQuiz = await source("features/task-content/ProfileCredentialsTask.jsx");
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");
  const taskFlow = await source("components/TaskFlow.jsx");
  const messages = await source("components/MessageCenter.jsx");
  const i18n = await source("i18n.jsx");

  assert.doesNotMatch(profileQuiz, /G01|来源系统|本系统|Mock/);
  assert.doesNotMatch(videoQuiz, /MOCK VIDEO|Mock ·|Mock learning/);
  assert.doesNotMatch(taskFlow, /MOCK CAMERA|站内课程 · MOCK|Take Mock photo|Mock 学习视频/);
  assert.doesNotMatch(messages, /MOCK DATA|>MOCK</);
  assert.doesNotMatch(i18n, /G01 的五项|来源系统|Mock 视频|Mock 数据|Mock 内容|Mock 练习|教师端 Mock/);
});
